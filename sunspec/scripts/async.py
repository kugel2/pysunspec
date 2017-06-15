import logging

import serial.tools.list_ports
import click
import twisted.internet.reactor
import twisted.internet.defer

import sunspec.core.client

# See file COPYING in this source tree
__copyright__ = 'Copyright 2017, EPC Power Corp.'
__license__ = 'GPLv2+'

twisted.internet.defer.setDebugging(True)


@click.group()
def cli():
    pass


def name_and_id_options(f):
    return click.option('--name', default='/dev/ttyUSB0')(
        click.option('--slave-id', default=1)(
            f
        )
    )


@cli.group(name='list')
def list_():
    pass


@list_.command()
def ports():
    for port in serial.tools.list_ports.comports():
        print(port)


@cli.command()
@name_and_id_options
def classic(name, slave_id):
    device = sunspec.core.client.SunSpecClientDevice(
        device_type=sunspec.core.client.RTU,
        slave_id=slave_id,
        name=name,
    )

    for model in [getattr(device, model) for model in device.models]:
        model.read()
        print_model(model)


@cli.command(name='twisted')
@name_and_id_options
def twisted_(name, slave_id):
    device = sunspec.core.client.SunSpecClientDevice(
        device_type=sunspec.core.client.RTU_TWISTED,
        slave_id=slave_id,
        name=name,
    )

    d = twisted.internet.task.deferLater(
        clock=twisted.internet.reactor,
        delay=0,
        callable=read_models,
        device=device,
    )
    d.addCallback(stop_reactor)
    d.addErrback(errbackhook)
    d.addErrback(stop_reactor, reason='exception raised')

    twisted.internet.reactor.callLater(3, stop_reactor, reason='timeout')

    return twisted.internet.reactor.run()


@twisted.internet.defer.inlineCallbacks
def read_models(device):
    yield device.device.scan()
    device.prep()

    for model in [getattr(device, model) for model in device.models]:
        yield model.read()
        print_model(model)


def stop_reactor(reason=None):
    if reason is not None:
        logging.warning('Exit forced due to: {}'.format(reason))

    twisted.internet.reactor.stop()


def errbackhook(error):
    logging.error(str(error))

    return error


def print_model(model):
    print('/---- Model {}'.format(model.name))
    print(str(model).strip())
    print('\---- end model {}'.format(model.name))
    print()
