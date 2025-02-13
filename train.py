import os
import tqdm
import numpy as np
import random 
import pickle

import torch
from torch import nn, optim

from torchtext.data import Field, BucketIterator
from torchtext.datasets import Multi30k

import datasets
import model2lstm
import argparse

if __name__ == '__main__':
    parser = argparse.ArgumentParser('Neural Machine Translation with Seq2Seq.')
    parser.add_argument('--gpu', type=str, default='0')
    parser.add_argument('--exp_dir', type=str, default='experiments/test')
    parser.add_argument('--dataset', type=str, choices=('iwslt2014', 'multi30k'), default='iwslt2014')
    parser.add_argument('--sample_size', type=int, default=None)
    parser.add_argument('--split_ratio', type=float, default=0.8)
    parser.add_argument('--training_source_lang', type=str,
                        default='de',
                        help='Path extension of supervised_training_data of our source language')
    parser.add_argument('--training_target_lang', type=str,
                        default='en',
                        help='Path extension of supervised_training_data of our target language')
    parser.add_argument('--model', type=str, choices=('Seq2Seq', 'BERT2LSTM','Seq2Seq_attention'), default='Seq2Seq')
    parser.add_argument('--batch_size', type=int, default=128)
    parser.add_argument('--train_path', type=str, default='data/iwslt2014/train.de-en.bpe')
    parser.add_argument('--dev_path', type=str, default='data/iwslt2014/dev.de-en.bpe')
    parser.add_argument('--test_path', type=str, default='data/iwslt2014/test.de-en.bpe')
    parser.add_argument('--n_epochs', type=int, default=10)
    parser.add_argument('--patience', type=int, default=10)
    parser.add_argument('--grad_clip', type=float, default=5.)
    args = parser.parse_args()

    print('Setting CUDA_VISIBLE_DEVICEdS...')
    os.environ['CUDA_VISIBLE_DEVICES'] = args.gpu
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    print('Done.')

    print('Creating dirs...')
    if not os.path.exists('experiments/test'):
        os.makedirs('experiments/test')
    print('Done.')

    if args.sample_size:
        print('Sampling %d examples of supervised training data...' % args.sample_size)
        supervised_data_source = list(open(args.train_path+'.de', 'rt'))
        supervised_data_target = list(open(args.train_path+ '.en', 'rt'))
        supervised_data = list(zip(supervised_data_source, supervised_data_target))
        random.shuffle(supervised_data)

        # to write the data
        fh = open(os.path.join(args.exp_dir, 'train.sample_data.%s' % args.training_source_lang), 'wt')
        [fh.write(i) for index, (i, j) in zip(range(0, int(args.sample_size*args.split_ratio)), supervised_data)]
        fh = open(os.path.join(args.exp_dir, 'train.sample_data.%s' % args.training_target_lang), 'wt')
        [fh.write(j) for index, (i, j) in zip(range(0, int(args.sample_size*args.split_ratio)), supervised_data)]

        fh = open(os.path.join(args.exp_dir, 'dev.sample_data.%s' % args.training_source_lang), 'wt')
        [fh.write(i) for index, (i, j) in zip(range(int(args.sample_size*args.split_ratio), args.sample_size), supervised_data)]
        fh = open(os.path.join(args.exp_dir, 'dev.sample_data.%s' % args.training_target_lang), 'wt')
        [fh.write(j) for index, (i, j) in zip(range(int(args.sample_size*args.split_ratio), args.sample_size), supervised_data)]
        train_path = args.exp_dir + '/train.sample_data'
        dev_path = args.exp_dir +'/dev.sample_data'

        print('Sampling Done.')
    else:
        train_path = args.train_path
        dev_path = args.dev_path

    print('Loading data...')
    dataset = args.dataset
    func = getattr(datasets, dataset)
    bert = 'BERT' in args.model
    (source, target), (train_iterator, val_iterator, test_iterator) = func(device, args.batch_size, train_path,
                                                                           dev_path, args.test_path, bert=bert)
    print('Done.')


    print('Saving config...')
    config = {'dataset': args.dataset}
    torch.save(config, os.path.join('experiments/test', 'config.pt'))
    print('Done.')

    print('Creating model...')
    name = args.model
    class_ = getattr(model2lstm, name)
    model = class_(input_vocab_size=len(source.vocab), output_vocab_size=len(target.vocab))
    model.to(device)
    print('Done.')

    print('Training:')
    optimizer = optim.Adam(model.parameters())
    target_pad_idx = target.vocab.stoi[target.pad_token]
    target_sos_idx = target.vocab.stoi['[CLS]']
    criterion = nn.CrossEntropyLoss(ignore_index=target_pad_idx, reduction='sum')

    patience = 0
    best_val_loss = float('inf')

    for epoch in range(1, args.n_epochs+1):
        model.train()
        acc_loss, total_toks, total_seqs = 0., 0, 0
        tqdm_iterator = tqdm.tqdm(train_iterator)
        for batch in tqdm_iterator:
            batch_input_seq, batch_input_len = batch.src
            batch_output_seq, batch_output_len = batch.trg

            logits_seq = model(batch_input_seq, batch_input_len, batch_output_seq, batch_output_len, training=True, device=device)
            loss = model.loss(logits_seq, batch_output_seq, criterion)

            optimizer.zero_grad()
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), args.grad_clip)
            optimizer.step()

            total_toks += int(batch_output_len.sum())
            total_seqs += batch_output_len.shape[0]
            acc_loss += loss.item()
            avg_tok_loss = acc_loss / total_toks
            avg_seq_loss = acc_loss / total_seqs
            tqdm_iterator.set_description('avg_tok_loss=%f, avg_seq_loss=%f' % (avg_tok_loss, avg_seq_loss))

        # validation
        model.eval()
        val_loss = 0.
        total_toks, total_seqs = 0, 0
        random_batch = random.randint(1, len(val_iterator))
        for i, batch in enumerate(tqdm.tqdm(val_iterator)):
            with torch.no_grad():
                batch_input_seq, batch_input_len = batch.src
                batch_output_seq, batch_output_len = batch.trg
                outputs, logits_seq = model(batch_input_seq, batch_input_len, output_seq=batch_output_seq, output_len=None, training=False,
                                            sos_tok=target_sos_idx, max_length=batch_output_seq.shape[0] - 1,
                                            device=device)

                loss = model.loss(logits_seq, batch_output_seq, criterion)
                val_loss += loss.item()

                total_toks += int(batch_output_len.sum())
                total_seqs += batch_output_len.shape[0]

                # generate the sequences
                if i+1 == random_batch:
                    saved_outputs = outputs
                    saved_batch_output_seq = np.array(batch_output_seq.tolist())

        # save best model
        if val_loss <= best_val_loss:
            print('Best validation loss achieved. Saving model...')
            best_val_loss = val_loss
            torch.save(model, 'experiments/test/model.pkl')
            print('Done.')
        else:
            patience += 1
            print("Validation loss increased. Patience is at %d." % patience)
            if patience >= args.patience:
                break

        # print a random batch
        itos = lambda x: target.vocab.itos[x]
        print('Randomly sampling some output...')
        print('=== Ground truth ===')
        print(np.vectorize(itos)(saved_batch_output_seq.T)[0:5])
        print('=== Predictions ===')
        print(np.vectorize(itos)(saved_outputs.T)[0:5])
        print('Done.')

        print('Total validation loss: %f' % val_loss)
        print('Average token loss: %f' % (val_loss / total_toks))
        print('Average sequence loss: %f' % (val_loss / total_seqs))
