"""Run MNIST-rot"""

import os
import sys
import time
sys.path.append('../')

import numpy as np
import tensorflow as tf

from io_helpers import download_dataset
from mnist_model import deep_mnist

def settings(opt):
	# Download MNIST if it doesn't exist
	opt['dataset'] = 'rotated_mnist'
	if not os.path.exists(opt['data_dir'] + '/mnist_rotation_new.zip'):
		download_dataset(opt)
	# Load dataset
	mnist_dir = opt['data_dir'] + '/mnist_rotation_new'
	train = np.load(mnist_dir + '/rotated_train.npz')
	valid = np.load(mnist_dir + '/rotated_valid.npz')
	test = np.load(mnist_dir + '/rotated_test.npz')
	data = {}
	data['train_x'] = train['x']
	data['train_y'] = train['y']
	data['valid_x'] = valid['x']
	data['valid_y'] = valid['y']
	data['test_x'] = test['x']
	data['test_y'] = test['y']
	
	# Other options
	if opt['load_settings']:
		opt['aug_crop'] = 0
		opt['n_epochs'] = 200
		opt['batch_size'] = 46
		opt['learning_rate'] = 0.0076
		opt['momentum'] = 0.93
		opt['std_mult'] = 0.7
		opt['delay'] = 12
		opt['psi_preconditioner'] = 7.8
		opt['filter_gain'] = 2
		opt['filter_size'] = 5
		opt['n_rings'] = 4
		opt['n_filters'] = 8
		opt['display_step'] = 10000/46
		opt['is_classification'] = True
		opt['combine_train_val'] = False
		opt['dim'] = 28
		opt['crop_shape'] = 0
		opt['n_channels'] = 1
		opt['n_classes'] = 10
		opt['lr_div'] = 10.

	opt['test_path'] = 'deep_mnist'
	opt['log_path'] = './logs/' + opt['test_path']
	opt['checkpoint_path'] = './checkpoints/' + opt['test_path']
	opt['test_path'] = './' + opt['test_path']
	return opt, data


def minibatcher(inputs, targets, batchsize, shuffle=False):
	assert len(inputs) == len(targets)
	if shuffle:
		indices = np.arange(len(inputs))
		np.random.shuffle(indices)
	for start_idx in range(0, len(inputs) - batchsize + 1, batchsize):
		if shuffle:
			excerpt = indices[start_idx:start_idx + batchsize]
		else:
			excerpt = slice(start_idx, start_idx + batchsize)
		yield inputs[excerpt], targets[excerpt]

def get_learning_rate(opt, current, best, counter, learning_rate):
	"""If have not seen accuracy improvement in delay epochs, then divide 
	learning rate by 10
	"""
	if current > best:
		best = current
		counter = 0
	elif counter > opt['delay']:
		learning_rate = learning_rate / opt['lr_div']
		counter = 0
	else:
		counter += 1
	return (best, counter, learning_rate)


def main(opt):
	"""The magic happens here"""
	tf.reset_default_graph()
	# SETUP AND LOAD DATA
	opt, data = settings(opt)
	
	# BUILD MODEL
	## Placeholders
	x = tf.placeholder(tf.float32, [opt['batch_size'],784], name='x')
	y = tf.placeholder(tf.int64, [opt['batch_size']], name='y')
	learning_rate = tf.placeholder(tf.float32, name='learning_rate')
	train_phase = tf.placeholder(tf.bool, name='train_phase')

	## Construct model and optimizer
	pred = deep_mnist(opt, x, train_phase)
	loss = tf.reduce_mean(tf.nn.sparse_softmax_cross_entropy_with_logits(logits=pred, labels=y))

	## Optimizer
	optim = tf.train.AdamOptimizer(learning_rate=learning_rate)
	grads_and_vars = optim.compute_gradients(loss)
	modified_gvs = []
	for g, v in grads_and_vars:
		if 'psi' in v.name:
			g = opt['psi_preconditioner']*g
		modified_gvs.append((g, v))
	train_op = optim.apply_gradients(modified_gvs)
	
	## Evaluation criteria
	correct_pred = tf.equal(tf.argmax(pred, 1), y)
	accuracy = tf.reduce_mean(tf.cast(correct_pred, tf.float32))
	
	# TRAIN
	init = tf.global_variables_initializer()
	init_local = tf.local_variables_initializer()

	# Configure tensorflow session
	config = tf.ConfigProto()
	config.gpu_options.allow_growth = True
	config.log_device_placement = False
	
	lr = opt['learning_rate']
	saver = tf.train.Saver()
	with tf.Session(config=config) as sess:
		sess.run([init, init_local], feed_dict={train_phase : True})
		
		start = time.time()
		epoch = 0
		step = 0.
		counter = 0
		best = 0.
		print('Starting training loop...')
		while epoch < opt['n_epochs']:
			# Training steps
			batcher = minibatcher(data['train_x'], data['train_y'], opt['batch_size'], shuffle=True)
			train_loss = 0.
			train_acc = 0.
			for i, (X, Y) in enumerate(batcher):
				feed_dict = {x: X, y: Y, learning_rate: lr, train_phase: True}
				__, l, a = sess.run([train_op, loss, accuracy], feed_dict=feed_dict)
				train_loss += l
				train_acc += a
				sys.stdout.write('{:d}/{:d}\r'.format(i, data['train_x'].shape[0]/opt['batch_size']))
				sys.stdout.flush()
			train_loss /= (i+1.)
			train_acc /= (i+1.)
			
			batcher = minibatcher(data['valid_x'], data['valid_y'], opt['batch_size'])
			valid_acc = 0.
			for i, (X, Y) in enumerate(batcher):
				feed_dict = {x: X, y: Y, train_phase: False}
				a = sess.run(accuracy, feed_dict=feed_dict)
				valid_acc += a
				sys.stdout.write('Validating\r')
				sys.stdout.flush()
			valid_acc /= (i+1.)
			
			print('[{:04d} | {:0.1f}] Loss: {:04f}, Train Acc.: {:04f}, Validation Acc.: {:04f}, Learning rate: {:.2e}'.format(epoch,
								time.time() - start, train_loss, train_acc, valid_acc, lr))
					
			# Save model
			if epoch % 10 == 0:
				saver.save(sess, opt['checkpoint_path'])
				print('Model saved')
			
			# Updates to the training scheme
			best, counter, lr = get_learning_rate(opt, valid_acc, best, counter, lr)
			epoch += 1
	
		# TEST
		batcher = minibatcher(data['test_x'], data['test_y'], opt['batch_size'])
		test_acc = 0.
		for i, (X, Y) in enumerate(batcher):
			feed_dict = {x: X, y: Y, train_phase: False}
			a = sess.run(accuracy, feed_dict=feed_dict)
			test_acc += a
			sys.stdout.write('Testing\r')
			sys.stdout.flush()
		test_acc /= (i+1.)
		
		print('Test Acc.: {:04f}'.format(test_acc))
	
	return valid_acc
		

if __name__ == '__main__':
	opt = {}
	opt['data_dir'] = './data'
	opt['load_settings'] = True
	main()





































