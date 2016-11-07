import os
import sys
import json
import time
import shutil
import pickle
import logging
import data_helper
import numpy as np
import pandas as pd
import tensorflow as tf
from text_cnn_rnn import TextCNNRNN
from sklearn.model_selection import train_test_split

logging.getLogger().setLevel(logging.INFO)

def train_cnn_rnn():
	start_time = time.time()
	input_file = sys.argv[1]
	x_, y_, vocabulary, vocabulary_inv, df, labels = data_helper.load_data(input_file)

	training_config = sys.argv[2]
	params = json.loads(open(training_config).read())

	# Assign a 300 dimension vector to each word
	word_embeddings = data_helper.load_embeddings(vocabulary)
	embedding_mat = [word_embeddings[word] for index, word in enumerate(vocabulary_inv)]
	embedding_mat = np.array(embedding_mat, dtype = np.float32)

	# Split the original dataset into train set and test set
	test_size = int(0.1 * len(x_))
	x, x_test = x_[:-test_size], x_[-test_size:]
	y, y_test = y_[:-test_size], y_[-test_size:]

	# Split the train set into train set and dev set
	shuffle_indices = np.random.permutation(np.arange(len(y)))
	x_shuffled = x[shuffle_indices]
	y_shuffled = y[shuffle_indices]
	x_train, x_dev, y_train, y_dev = train_test_split(x_shuffled, y_shuffled, test_size=0.1)

	logging.info('x_train: {}, x_dev: {}, x_test: {}'.format(len(x_train), len(x_dev), len(x_test)))
	logging.info('y_train: {}, y_dev: {}, y_test: {}'.format(len(y_train), len(y_dev), len(y_test)))

	# Create a directory, everything related to the training will be saved in this directory
	timestamp = str(int(time.time()))
	trained_dir = './trained_results_' + timestamp + '/'
	if os.path.exists(trained_dir):
		shutil.rmtree(trained_dir)
	os.makedirs(trained_dir)

	df_train, df_test = df[:-test_size], df[-test_size:]
	df_train.to_csv(trained_dir + 'data_train.csv', index=False, sep='|')
	df_test.to_csv(trained_dir + 'data_test.csv', index=False, sep='|')

	graph = tf.Graph()
	with graph.as_default():
		session_conf = tf.ConfigProto(allow_soft_placement=True, log_device_placement=False)
		sess = tf.Session(config=session_conf)
		with sess.as_default():
			cnn_rnn = TextCNNRNN(
				embedding_mat=embedding_mat,
				sequence_length=x_train.shape[1],
				num_classes = y_train.shape[1],
				non_static=params['non_static'],
				hidden_unit=params['hidden_unit'],
				max_pool_size=params['max_pool_size'],
				filter_sizes=map(int, params['filter_sizes'].split(",")),
				num_filters = params['num_filters'],
				embedding_size = params['embedding_dim'],
				l2_reg_lambda = params['l2_reg_lambda'])

			global_step = tf.Variable(0, name='global_step', trainable=False)
			optimizer = tf.train.RMSPropOptimizer(1e-3, decay=0.9)
			grads_and_vars = optimizer.compute_gradients(cnn_rnn.loss)
			train_op = optimizer.apply_gradients(grads_and_vars, global_step=global_step)

			# Checkpoint files will be saved in this directory during training
			checkpoint_dir = './checkpoints_' + timestamp + '/'
			if os.path.exists(checkpoint_dir):
				shutil.rmtree(checkpoint_dir)
			os.makedirs(checkpoint_dir)
			checkpoint_prefix = os.path.join(checkpoint_dir, 'model')

			def real_len(batches):
				return [np.ceil(np.argmin(batch + [0]) * 1.0 / params['max_pool_size']) for batch in batches]

			def train_step(x_batch, y_batch):
				feed_dict = {
					cnn_rnn.input_x: x_batch,
					cnn_rnn.input_y: y_batch,
					cnn_rnn.dropout_keep_prob: params['dropout_keep_prob'],
					cnn_rnn.batch_size: len(x_batch),
					cnn_rnn.pad: np.zeros([len(x_batch), 1, params['embedding_dim'], 1]),
					cnn_rnn.real_len: real_len(x_batch),
				}
				_, step, loss, accuracy = sess.run([train_op, global_step, cnn_rnn.loss, cnn_rnn.accuracy], feed_dict)

			def dev_step(x_batch, y_batch):
				feed_dict = {
					cnn_rnn.input_x: x_batch,
					cnn_rnn.input_y: y_batch,
					cnn_rnn.dropout_keep_prob: 1.0,
					cnn_rnn.batch_size: len(x_batch),
					cnn_rnn.pad: np.zeros([len(x_batch), 1, params['embedding_dim'], 1]),
					cnn_rnn.real_len: real_len(x_batch),
				}
				step, loss, accuracy, num_correct, predictions = sess.run(
					[global_step, cnn_rnn.loss, cnn_rnn.accuracy, cnn_rnn.num_correct, cnn_rnn.predictions], feed_dict)
				return accuracy, loss, num_correct, predictions

			saver = tf.train.Saver(tf.all_variables())
			sess.run(tf.initialize_all_variables())

			# Training starts here
			train_batches = data_helper.batch_iter(list(zip(x_train, y_train)), params['batch_size'], params['num_epochs'])
			best_accuracy, best_at_stp = 0, 0

			# Train the model with x_train and y_train
			for train_batch in train_batches:
				x_train_batch, y_train_batch = zip(*train_batch)
				train_step(x_train_batch, y_train_batch)
				current_step = tf.train.global_step(sess, global_step)

				# Evaluate the model with x_dev and y_dev
				if current_step % params['evaluate_every'] == 0:
					dev_batches = data_helper.batch_iter(list(zip(x_dev, y_dev)), params['batch_size'], 1)

					total_dev_correct = 0
					for dev_batch in dev_batches:
						x_dev_batch, y_dev_batch = zip(*dev_batch)
						acc, loss, num_dev_correct, predictions = dev_step(x_dev_batch, y_dev_batch)
						total_dev_correct += num_dev_correct

					accuracy = float(total_dev_correct) / len(y_dev)
					logging.info('Accuracy on dev set: {}'.format(accuracy))

					if accuracy >= best_accuracy:
						best_accuracy, best_at_step = accuracy, current_step
						path = saver.save(sess, checkpoint_prefix, global_step=current_step)
						logging.critical('Saved model {} at step {}'.format(path, best_at_step))
						logging.critical('Best accuracy {} at step {}'.format(best_accuracy, best_at_step))

			logging.critical('Training is complete, testing the best model on x_test and y_test')
			end_time = time.time()

			# Evaluate x_test and y_test
			saver.restore(sess, checkpoint_prefix + '-' + str(best_at_step))

			test_batches = data_helper.batch_iter(list(zip(x_test, y_test)), params['batch_size'], 1, shuffle=False)
			total_test_correct, predicted_labels = 0, []

			for test_batch in test_batches:
				x_test_batch, y_test_batch = zip(*test_batch)
				acc, loss, num_test_correct, predictions = dev_step(x_test_batch, y_test_batch)
				total_test_correct += int(num_test_correct)
				for prediction in predictions:
					predicted_labels.append(labels[prediction])

			df_test['PREDICTED'] = predicted_labels
			columns = sorted(df_test.columns, reverse=True)
			df_test.to_csv(trained_dir + 'predictions_all.csv', index=False, columns=columns, sep='|')

			df_test_correct = df_test[df_test['PREDICTED'] == df_test['Category']]
			df_test_correct.to_csv(trained_dir + 'predictions_correct.csv', index=False, columns=columns, sep='|')

			df_test_non_correct = df_test[df_test['PREDICTED'] != df_test['Category']]
			df_test_non_correct.to_csv(trained_dir + 'predictions_non_correct.csv', index=False, columns=columns, sep='|')

			# Generate a classification report after predicting on the test set
			reports, summary = [], {}
			summary['total_correct'], summary['total_non_correct'] = df_test_correct.shape[0], df_test_non_correct.shape[0]
			summary['total_test_examples'] = df_test.shape[0]
			summary['accuracy'] = float(summary['total_correct']) / summary['total_test_examples']
			summary['training_time'] = int(end_time - start_time)
			reports.append(summary)

			total_counts = df_test['PREDICTED'].value_counts().to_dict()
			correct_counts = df_test_correct['PREDICTED'].value_counts().to_dict()
			non_correct_counts = df_test_non_correct['PREDICTED'].value_counts().to_dict()

			for key in labels:
				report = {}
				report['label'], report['total'] = key, int(total_counts.get(key, 0))
				report['correct'], report['non_correct'] = int(correct_counts.get(key, 0)), int(non_correct_counts.get(key, 0))
				reports.append(report)

			with open(trained_dir + 'classification_report.json', 'w') as outfile:
				json.dump(reports, outfile, indent=4)

			logging.critical('Accuracy on test set: {}'.format(float(total_test_correct) / len(y_test)))

	# Save trained parameters and files since predict.py needs them
	with open(trained_dir + 'words_index.json', 'w') as outfile:
		json.dump(vocabulary, outfile, indent=4, ensure_ascii=False)
	with open(trained_dir + 'embeddings.pickle', 'wb') as outfile:
		pickle.dump(embedding_mat, outfile, pickle.HIGHEST_PROTOCOL)
	with open(trained_dir + 'labels.json', 'w') as outfile:
		json.dump(labels, outfile, indent=4, ensure_ascii=False)

	os.rename(path, trained_dir + 'best_model.ckpt')
	os.rename(path + '.meta', trained_dir + 'best_model.meta')
	shutil.rmtree(checkpoint_dir)
	logging.critical('{} has been removed'.format(checkpoint_dir))

	params['sequence_length'] = x_train.shape[1]
	with open(trained_dir + 'trained_parameters.json', 'w') as outfile:
		json.dump(params, outfile, indent=4, sort_keys=True, ensure_ascii=False)

	logging.critical('The training is complete, all files have been saved at {}'.format(trained_dir))

if __name__ == '__main__':
	train_cnn_rnn()
