#!/usr/bin/python
# -*- coding: UTF-8 -*-

# Imports
from __future__ import print_function
from keras import backend as TK
from keras.layers import Input, merge
from keras.layers import Dense, Lambda, Reshape, Activation, Flatten, Dropout
from keras.layers.convolutional import Convolution1D, MaxPooling1D
from keras.models import Model, Sequential, Graph

from keras.layers.embeddings import Embedding
from keras import callbacks
import theano.tensor as tt
import numpy as np
from config import Configuration

from data_helpers import DataHelpers

np.random.seed(1337)  # for reproducibility

# Object Initialization
conf = Configuration()
dh = DataHelpers()

# Neural Network Parameters.
# Word Embedding Size
embedding_dim = 100

# Training Parameters
batch_size = 128
nb_epoch = 5

# CNN Model Parameters
filter_sizes = (3, 4)
num_filters = 150
dropout_prob = (0.25, 0.5)
hidden_dims = 150
J = 3
GAMMA = 10


'''
Note:
batch_size is highly sensitive.
Only 2 epochs are needed as the dataset is very small.
'''
embeddings_pickled=False
vocab_pickled = False

embedding_weights_masking=False # Mask the Embedding Weights If false, data get written from zero-index of the array.

embedding_weights, vocab_index_dict = dh.get_vocab_index_embedding_weights(embedding_dim, embedding_weights_masking, load_embeddings_pickled=embeddings_pickled, load_vocab_pickled=vocab_pickled)

# Size of Embeddings Weights layer, also the size of the vocabulary
vocab_size = embedding_weights.shape[0]
print('Vocabulary Shape:', vocab_size)


def R(vects):
    def _squared_magnitude(x):
        return tt.sqr(x).sum(axis=-1)

    def _magnitude(x):
        return tt.sqrt(
            tt.maximum(
                _squared_magnitude(x),
                np.finfo(
                    x.dtype).tiny))

    def _cosine(x, y):
        return tt.clip((x * y).sum(axis=-1) /
                       (_magnitude(x) * _magnitude(y)), 0, 1)

    return _cosine(*vects).reshape([-1, 1])


def model(sequence_length=None):
    graph = Graph()
    graph.add_input(name='input', input_shape=(sequence_length, embedding_dim))
    for fsz in filter_sizes:
        conv = Convolution1D(nb_filter=num_filters,
                             filter_length=fsz,
                             border_mode='valid',
                             activation='relu',
                             subsample_length=1,
                             input_dim=embedding_dim,
                             input_length=sequence_length)
        pool = MaxPooling1D(pool_length=sequence_length - fsz + 1)
        graph.add_node(conv, name='conv-%s' % fsz, input='input')
        graph.add_node(pool, name='maxpool-%s' % fsz, input='conv-%s' % fsz)
        graph.add_node(
            Flatten(),
            name='flatten-%s' %
            fsz,
            input='maxpool-%s' %
            fsz)

    if len(filter_sizes) > 1:
        graph.add_output(name='output',
                         inputs=['flatten-%s' % fsz for fsz in filter_sizes],
                         merge_mode='concat')
    else:
        graph.add_output(name='output', input='flatten-%s' % filter_sizes[0])

    # main sequential model
    model = Sequential()
    model.add(
        Embedding(
            vocab_size,
            embedding_dim,
            input_length=sequence_length,
            weights=[embedding_weights]))
    model.add(
        Dropout(
            dropout_prob[0],
            input_shape=(
                sequence_length,
                embedding_dim)))
    model.add(graph)
    model.add(Dense(hidden_dims))
    model.add(Dropout(dropout_prob[1]))
    model.add(Activation('relu'))
    return model

# Input Layer with all the query, similar and non similar documents.
query = Input(shape=(conf.query_length,), dtype='int32')
pos_doc = Input(shape=(conf.document_length,), dtype='int32')
neg_docs = [Input(shape=(conf.document_length,), dtype='int32') for _ in xrange(0, conf.num_negative_examples)]

query_model = model(sequence_length=conf.query_length)
doc_model = model(sequence_length=conf.document_length)


pos_doc_sem = doc_model(pos_doc)
neg_doc_sems = [doc_model(neg_docs[i]) for i in xrange(0, conf.num_negative_examples)]

query_sem = query_model(query)


R_layer = Lambda(R, output_shape=(1,))  # See equation (4).
R_Q_D_p = R_layer([query_sem, pos_doc_sem])  # See equation (4).

# See equation (4).
R_Q_D_ns = [R_layer([query_sem, neg_doc_sem]) for neg_doc_sem in neg_doc_sems]
concat_Rs = merge([R_Q_D_p] + R_Q_D_ns, mode="concat", concat_axis=1)
concat_Rs = Reshape((J + 1,))(concat_Rs)

# See equation (5).
with_gamma = Lambda(lambda x: x * GAMMA, output_shape=(J + 1,))(concat_Rs)

# See equation (5).
exponentiated = Lambda(lambda x: TK.exp(x), output_shape=(J + 1,))(with_gamma)
exponentiated = Reshape((J + 1,))(exponentiated)

# See equation (5).
prob = Lambda(lambda x: TK.expand_dims(
    x[:, 0] / TK.sum(x, axis=1), 1), output_shape=(1,))(exponentiated)

inputs = [query, pos_doc] + neg_docs

# Model Compile
model = Model(input=inputs, output=prob)
model.compile(optimizer="adam", loss="binary_crossentropy")
print("Model Compiled!")
# Model Summary
#print(model.summary())

# verbose: 0 for no logging to stdout, 1 for progress bar logging, 2 for one log line per epoch.
print('Start Training ...')
gg_train = dh.load_data_generator(vocab_index_dict, mode='training', batch_size=128,nb_epochs = 15)
gg_validate = dh.load_data_generator(vocab_index_dict, mode='validation', batch_size=128, nb_epochs = 15)

print("Fitting model using a data generator ..")
chkpoint = callbacks.ModelCheckpoint(conf.trained_model_dir  + '/weights.{epoch:02d}.hdf5', verbose=1)
hist = model.fit_generator(gg_train, nb_epoch=15, samples_per_epoch=1050916, validation_data=gg_validate, nb_val_samples=262729, callbacks=[chkpoint], verbose=1)
# History Call back to record: training / validation loss / accuracy at each epoch.
print(hist.history)
print('Model Fitting Completed! Now saving trained Model on Disk ... ')
# Save the model configuration and model weights.
model.save('{}/cnn_model_dssm.h5'.format(conf.trained_model_dir))  # creates a HDF5 file

# save model configuration and model weights seperately.
fw = open("{}/cnn_dssm_model_only.json".format(conf.trained_model_dir), "w")
json_string = model.to_json()
fw.write(json_string)
fw.close()
model.save_weights('{}/cnn_dssm_model_weights.h5'.format(conf.trained_model_dir)) # creates a HDF5 file