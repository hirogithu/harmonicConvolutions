'''Run BSD500'''

import os
import sys
import time
sys.path.append('../')

import cPickle as pkl
import numpy as np
import skimage.io as skio
import tensorflow as tf

from io_helpers import download_dataset, load_pkl, pklbatcher
from BSD_model import hnet_bsd, vgg_bsd


#-----------ARGS----------
flags = tf.app.flags
FLAGS = flags.FLAGS
#execution modes
flags.DEFINE_string('mode', 'hnet', 'run "hnet" or "baseline"')
flags.DEFINE_string('save_name', 'my_model', 'save directory')

def settings(opt):
	tf.reset_default_graph()
	# Default configuration
	if opt['load_settings']:
		opt['combine_train_val'] = True #False	
		
		opt['learning_rate'] = 1e-2
		opt['batch_size'] = 10
		opt['std_mult'] = 1
		opt['psi_preconditioner'] = 3.4
		opt['delay'] = 8
		opt['display_step'] = 8
		opt['save_step'] = 10
		opt['n_epochs'] = 250
		opt['dim'] = 321
		opt['dim2'] = 481
		opt['n_channels'] = 3
		opt['n_classes'] = 10
		#opt['n_filters'] = 8
		opt['n_filters'] = 7
	
		opt['filter_size'] = 3
		opt['n_rings'] = 2
		opt['filter_gain'] = 2
		opt['augment'] = True
		opt['lr_div'] = 10.
		opt['sparsity'] = 1.
		opt['test_path'] = FLAGS.save_name
		opt['log_path'] = './logs/' + opt['test_path']
		opt['checkpoint_path'] = './checkpoints/' + opt['test_path']
		opt['test_path'] = './' + opt['test_path']
		
	if not os.path.exists(opt['test_path']):
		os.mkdir(opt['test_path'])
	if not os.path.exists(opt['log_path']):
		os.mkdir(opt['log_path'])
	if not os.path.exists(opt['checkpoint_path']):
		os.mkdir(opt['checkpoint_path'])
		
	data = load_pkl('./data', 'bsd_pkl_float', prepend='')
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
		learning_rate = learning_rate / 10.
		counter = 0
	else:
		counter += 1
	return (best, counter, learning_rate)


def sparsity_regularizer(x, sparsity):
	"""Define a sparsity regularizer"""
	q = tf.reduce_mean(tf.nn.sigmoid(x))
	return -sparsity*tf.log(q) - (1-sparsity)*tf.log(1-q)


def main(opt):
	"""The magic happens here"""
	tf.reset_default_graph()
	# SETUP AND LOAD DATA	
	opt, data = settings(opt)
	
	# BUILD MODEL
	## Placeholders
	x = tf.placeholder(tf.float32, [opt['batch_size'],None,None,3], name='x')
	y = tf.placeholder(tf.float32, [opt['batch_size'],None,None,1], name='y')
	learning_rate = tf.placeholder(tf.float32, name='learning_rate')
	train_phase = tf.placeholder(tf.bool, name='train_phase')

	## Construct model
	if FLAGS.mode == 'baseline':
		pred = vgg_bsd(opt, x, train_phase)
	elif FLAGS.mode == 'hnet':
		pred = hnet_bsd(opt, x, train_phase)
	else:
		print('Must execute script with valid --mode flag: "hnet" or "baseline"')
		sys.exit(-1)
	
	# Print number of parameters
	n_vars = 0
	for var in tf.get_collection(tf.GraphKeys.GLOBAL_VARIABLES):
		n_vars += np.prod(var.get_shape().as_list())
	print('Number of parameters: {:d}'.format(n_vars))
	
	loss = 0.
	beta = 1-tf.reduce_mean(y)
	pw = beta / (1. - beta)
	for key in pred.keys():
		pred_ = pred[key]
		loss += tf.reduce_mean(tf.nn.weighted_cross_entropy_with_logits(y, pred_, pw))
		# Sparsity regularizer
		loss += opt['sparsity']*sparsity_regularizer(pred_, 1-beta)
	## Optimizer
	optim = tf.train.AdamOptimizer(learning_rate=learning_rate)
	grads_and_vars = optim.compute_gradients(loss)
	modified_gvs = []
	for g, v in grads_and_vars:
		if 'psi' in v.name:
			g = opt['psi_preconditioner']*g
		modified_gvs.append((g, v))
	train_op = optim.apply_gradients(modified_gvs)
	
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
		print('Starting training loop...')
		while epoch < opt['n_epochs']:
			anneal = 0.1 + np.minimum(epoch/30.,1.)
			# Training steps
			batcher = pklbatcher(data['train_x'], data['train_y'], opt['batch_size'], shuffle=True, augment=True)
			train_loss = 0.
			for i, (X, Y, __) in enumerate(batcher):
				feed_dict = {x: X, y: Y, learning_rate: lr, train_phase: True}
				__, l = sess.run([train_op, loss], feed_dict=feed_dict)
				train_loss += l
				sys.stdout.write('{:d}/{:d}\r'.format(i, len(data['train_x'].keys())/opt['batch_size']))
				sys.stdout.flush()
			train_loss /= (i+1.)
			
			print('[{:04d} | {:0.1f}] Loss: {:04f}, Learning rate: {:.2e}'.format(epoch,
								time.time() - start, train_loss, lr))
			
			
			if epoch % opt['save_step'] == 0:
				# Validate
				save_path = opt['test_path'] + '/T_' + str(epoch)
				if not os.path.exists(save_path):
					os.mkdir(save_path)
				generator = pklbatcher(data['valid_x'], data['valid_y'],
											  opt['batch_size'], shuffle=False,
											  augment=False, img_shape=(opt['dim'], opt['dim2']))
				# Use sigmoid to map to [0,1]
				bsd_map = tf.nn.sigmoid(pred['fuse'])
				j = 0
				for batch in generator:
					batch_x, batch_y, excerpt = batch
					output = sess.run(bsd_map, feed_dict={x: batch_x, train_phase: False})
					for i in xrange(output.shape[0]):
						save_name = save_path + '/' + str(excerpt[i]).replace('.jpg','.png')
						im = output[i,:,:,0]
						im = (255*im).astype('uint8')
						if data['valid_x'][excerpt[i]]['transposed']:
							im = im.T
						skio.imsave(save_name, im)
						j += 1
				print('Saved predictions to: %s' % (save_path,))
			
			# Updates to the training scheme
			if epoch % 40 == 39:
				lr = lr / 10.
			epoch += 1
			
			# Save model
			saver.save(sess, opt['checkpoint_path'] + 'model.ckpt')
	return train_loss, n_vars

if __name__ == '__main__':
	opt = {}
	opt['data_dir'] = './data'
	opt['load_settings'] = True
	main(opt)





































