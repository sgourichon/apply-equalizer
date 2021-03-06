#!/usr/bin/env python3
# -*- coding: utf-8 -*-


import dbus, os
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GObject
from subprocess import call, check_call
from time import sleep
from xdg.BaseDirectory import *

config_dir = os.path.join(xdg_config_home, 'apply-equalizer')
if not os.path.isdir(config_dir):
	os.mkdir(config_dir)

eq_config_path = os.path.join(xdg_config_home, 'pulse', 'equalizerrc')

def get_bus_address():
	""" lookup PA address """
	return dbus.SessionBus()\
		.get_object(
			'org.PulseAudio1', 
			'/org/pulseaudio/server_lookup1'
		).Get(
			'org.PulseAudio.ServerLookup1',
			'Address',
			dbus_interface=dbus.PROPERTIES_IFACE
		)

def wait_for_pulseaudio ():
	""" wait for pulseaudio start (possibly forever) """
	while call(['pulseaudio', '--check']) == 1:
		print ('pulseaudio is not running, retry...')
		sleep(1)

def connect(srv_addr=None):
	"""
	load dbus-module into PA, lookup address and connect to the dbus interface
	"""
		
	# load dbus-module if not loaded already
	if call('pactl list modules short | grep module-dbus-protocol', shell=True) == 1:
		print('load dbus-module into PA')
		check_call(['pactl', 'load-module', 'module-dbus-protocol'])
	
	while not srv_addr:
		try:
			srv_addr = get_bus_address()
			print('Got pa-server address from dbus: {}'.format(srv_addr))
		except dbus.exceptions.DBusException as err:
			if err.get_dbus_name() != 'org.freedesktop.DBus.Error.ServiceUnknown':
				raise
			print('cannot look up address!')
		sleep(1)	
	
	return dbus.connection.Connection(srv_addr)


def init ():
	""" connect to PA-DBus, set up event listeners and configure default sink eq """
	global bus, core
	# connect to pulseaudio dbus
	wait_for_pulseaudio()
	bus = connect()
	core = bus.get_object(object_path='/org/pulseaudio/core1')
	bus.call_on_disconnection(on_disconnect)
	
	# listen for port change events i.e. headphone is plugged in or out
	bus.add_signal_receiver(on_port_change, 'ActivePortUpdated')
	core.ListenForSignal('org.PulseAudio.Core1.Device.ActivePortUpdated', dbus.Array(signature='o'))
		
	configure_default_sink()
	
	print('connected to pulseaudio')
	
def configure_default_sink():
	""" activates eq conf for current active sink """
	# activate profile for current audio device and port
	fb_sink_addr = core.Get('org.PulseAudio.Core1', 'FallbackSink', dbus_interface=dbus.PROPERTIES_IFACE)
	fb_sink = bus.get_object(object_path=fb_sink_addr)
	fb_sink_name = getName(fb_sink, 'Device')
	try:
		port_addr = fb_sink.Get('org.PulseAudio.Core1.Device', 'ActivePort', dbus_interface=dbus.PROPERTIES_IFACE)
		port = bus.get_object(object_path=port_addr)
		port_name = port.Get('org.PulseAudio.Core1.DevicePort', 'Name', dbus_interface=dbus.PROPERTIES_IFACE)
		activate_profile(fb_sink_name, port_name)
	except dbus.exceptions.DBusException as e:
		print ("current device has no ports!")

def on_disconnect (con):
	print ('disconnected from pulseaudio, try to reconnect...')
	init()
	
pendingChange=False
requestedPortAddr=None

def on_port_change(port_addr):
	""" save last port change """
	print ('on_port_change: received event from pulseaudio')
	global requestedPortAddr, pendingChange
	requestedPortAddr = port_addr
	if pendingChange == False:
		GObject.idle_add(apply_requested_port_change)
		pendingChange = True

def apply_requested_port_change():
	global requestedPortAddr
	print ('apply_requested_port_change: {}'.format(requestedPortAddr))
	apply_port_change(requestedPortAddr)
	global pendingChange
	pendingChange = False
	return False

def apply_port_change(port_addr):
	sink_addr = os.path.dirname(port_addr)
	sink = bus.get_object(object_path=sink_addr)
	sink_name = getName(sink, 'Device')
	
	port = bus.get_object(object_path=port_addr)
	port_name = getName(port, 'DevicePort')
	
	print ("change detected! new output port is '{}' on '{}'".format(port_name, sink_name))
	
	activate_profile(sink_name, port_name)


def getName (obj, itf):
	return obj.Get('org.PulseAudio.Core1.{}'.format(itf),
				'Name', dbus_interface=dbus.PROPERTIES_IFACE)

def make_conf_path(sink_name, port_name):
	""" get path to port-specific eq conf """
	return os.path.join(config_dir, sink_name, port_name, 'equalizerrc')


def activate_profile(sink_name, port_name):
	""" create symlink to port-specific eq conf """
	# dump old configuration and save content
	os.system('pulseaudio-equalizer interface.getsettings')
	current_conf = open(eq_config_path).read()
	
	# remove old config
	try:
		os.remove(eq_config_path)
	except OSError:
		pass
	
	conf_file = make_conf_path(sink_name, port_name)
	conf_dir = os.path.dirname(conf_file)
	
	# create new config if it does not exist and fill with old config
	if not os.path.isdir(conf_dir):
		os.makedirs(conf_dir)
		open(conf_file, 'w').write(current_conf)
	
	# create symlink
	os.symlink(conf_file, eq_config_path)
	
	# apply new settings
	os.system('pulseaudio-equalizer interface.applysettings')
	

DBusGMainLoop(set_as_default=True)
loop = GObject.MainLoop()

init()

loop.run()
