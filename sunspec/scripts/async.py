from __future__ import print_function

import logging

import click
import serial.tools.list_ports
import twisted.internet.defer
import twisted.internet.reactor

import sunspec.core.client

# See file COPYING in this source tree
__copyright__ = 'Copyright 2017, EPC Power Corp.'
__license__ = 'GPLv2+'

twisted.internet.defer.setDebugging(True)

# logging.getLogger().setLevel(logging.DEBUG)


@click.group()
def cli():
    pass


name_and_id_options = (
    click.option('--name', default='/dev/ttyUSB0'),
    click.option('--slave-id', default=1),
    click.option('--write', type=(str, str, float), default=(None,) * 3),
)


def apply_options(*options):
    def g(f):
        for option in reversed(options):
            f = option(f)

        return f

    return g


@cli.group(name='list')
def list_():
    pass


@list_.command()
def ports():
    for port in serial.tools.list_ports.comports():
        print(port)


@cli.command()
@apply_options(*name_and_id_options)
def classic(name, slave_id, write):
    device = sunspec.core.client.SunSpecClientDevice(
        device_type=sunspec.core.client.RTU,
        slave_id=slave_id,
        name=name,
    )

    for model in [getattr(device, model) for model in device.models]:
        model.read()
        print_model(model)

    if None not in write:
        model, point, value = write
        model = getattr(device, model)

        def get_point():
            return getattr(model, point)

        def set_point(v):
            setattr(model, point, v)

        print('Testing by writing to:')
        print('    {} -> {}/{}'.format(value, model, point))
        print()

        model.read()
        original = get_point()
        print('              Read: {}'.format(get_point()))

        set_point(value)
        model.write()
        print('Assigned and wrote: {}'.format(get_point()))

        model.read()
        print('              Read: {}'.format(get_point()))

        set_point(original)
        model.write()
        print('Assigned and wrote: {}'.format(get_point()))

        model.read()
        print('              Read: {}'.format(get_point()))


@cli.command(name='twisted')
@apply_options(*name_and_id_options)
def twisted_(name, slave_id, write):
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

    if None not in write:
        model, point, value = write
        d.addCallback(
            lambda _: write_something(
                device=device,
                model=model,
                point=point,
                value=value,
            )
        )

    d.addCallback(stop_reactor)
    d.addErrback(errbackhook)
    d.addErrback(lambda _: stop_reactor(reason='exception raised'))

    twisted.internet.reactor.callLater(5, stop_reactor, reason='timeout')

    return twisted.internet.reactor.run()


@twisted.internet.defer.inlineCallbacks
def read_models(device):
    yield device.device.scan()
    device.prep()

    for model in [getattr(device, model) for model in device.models]:
        yield model.read()
        print_model(model)


@twisted.internet.defer.inlineCallbacks
def write_something(device, model, point, value):
    print('Testing by writing:')
    print('    {} -> {}/{}'.format(value, model, point))
    print()
    
    model = getattr(device, model)

    def get_point():
        return getattr(model, point)

    def set_point(v):
        setattr(model, point, v)

    yield model.read()
    original = get_point()
    print('              Read: {}'.format(get_point()))

    set_point(value)
    yield model.write()
    print('Assigned and wrote: {}'.format(get_point()))

    yield model.read()
    print('              Read: {}'.format(get_point()))

    set_point(original)
    yield model.write()
    print('Assigned and wrote: {}'.format(get_point()))

    yield model.read()
    print('              Read: {}'.format(get_point()))


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
