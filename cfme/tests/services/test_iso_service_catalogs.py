# -*- coding: utf-8 -*-
import fauxfactory
import pytest

from widgetastic.utils import partial_match

from cfme import test_requirements
from cfme.infrastructure.provider import InfraProvider
from cfme.infrastructure.provider.rhevm import RHEVMProvider
from cfme.infrastructure.pxe import get_template_from_config, ISODatastore
from cfme.services.service_catalogs import ServiceCatalogs
from cfme.utils import testgen
from cfme.utils.blockers import GH
from cfme.utils.conf import cfme_data
from cfme.utils.generators import random_vm_name
from cfme.utils.log import logger

pytestmark = [
    pytest.mark.meta(server_roles="+automate"),
    pytest.mark.usefixtures('uses_infra_providers'),
    test_requirements.service,
    pytest.mark.tier(2)
]


def pytest_generate_tests(metafunc):
    # Filter out providers without provisioning data or hosts defined
    argnames, argvalues, idlist = testgen.providers_by_class(
        metafunc, [InfraProvider], required_fields=[
            'iso_datastore',
            ['provisioning', 'host'],
            ['provisioning', 'datastore'],
            ['provisioning', 'iso_template'],
            ['provisioning', 'iso_file'],
            ['provisioning', 'iso_kickstart'],
            ['provisioning', 'iso_root_password'],
            ['provisioning', 'iso_image_type'],
            ['provisioning', 'vlan'],
        ])

    new_idlist = []
    new_argvalues = []
    for i, argvalue_tuple in enumerate(argvalues):
        args = dict(zip(argnames, argvalue_tuple))

        iso_cust_template = args['provider'].data['provisioning']['iso_kickstart']
        if iso_cust_template not in cfme_data.get('customization_templates', {}).keys():
            continue

        new_idlist.append(idlist[i])
        new_argvalues.append(argvalues[i])

    testgen.parametrize(metafunc, argnames, new_argvalues, ids=new_idlist, scope="module")


@pytest.fixture(scope="module")
def iso_cust_template(provider, appliance):
    iso_cust_template = provider.data['provisioning']['iso_kickstart']
    return get_template_from_config(iso_cust_template, appliance=appliance)


@pytest.fixture(scope="module")
def iso_datastore(provider, appliance):
    return ISODatastore(provider.name, appliance=appliance)


@pytest.fixture(scope="function")
def setup_iso_datastore(setup_provider, iso_cust_template, iso_datastore, provisioning):
    if not iso_datastore.exists():
        iso_datastore.create()
    iso_datastore.set_iso_image_type(provisioning['iso_file'], provisioning['iso_image_type'])
    if not iso_cust_template.exists():
        iso_cust_template.create()


@pytest.fixture(scope="function")
def catalog_item(appliance, provider, dialog, catalog, provisioning):
    iso_template, host, datastore, iso_file, iso_kickstart,\
        iso_root_password, iso_image_type, vlan = map(provisioning.get, ('pxe_template', 'host',
                                'datastore', 'iso_file', 'iso_kickstart',
                                'iso_root_password', 'iso_image_type', 'vlan'))

    provisioning_data = {
        'catalog': {'catalog_name': {'name': iso_template, 'provider': provider.name},
                    'vm_name': random_vm_name('iso_service'),
                    'provision_type': 'ISO',
                    'iso_file': {'name': iso_file}},
        'environment': {'host_name': {'name': host},
                        'datastore_name': {'name': datastore}},
        'customize': {'custom_template': {'name': iso_kickstart},
                      'root_password': iso_root_password},
        'network': {'vlan': partial_match(vlan)},
    }

    item_name = fauxfactory.gen_alphanumeric()
    return appliance.collections.catalog_items.create(
        appliance.collections.catalog_items.RHV,
        name=item_name,
        description="my catalog", display_in=True, catalog=catalog,
        dialog=dialog,
        prov_data=provisioning_data
    )


@pytest.mark.rhv1
@pytest.mark.meta(blockers=[GH('ManageIQ/integration_tests:6692',
                               unblock=lambda provider: not provider.one_of(RHEVMProvider))])
def test_rhev_iso_servicecatalog(appliance, provider, catalog_item, setup_iso_datastore, request):
    """Tests RHEV ISO service catalog

    Metadata:
        test_flag: iso, provision
    """
    vm_name = catalog_item.prov_data['catalog']["vm_name"]
    request.addfinalizer(
        lambda: appliance.collections.infra_vms.instantiate(
            "{}_0001".format(vm_name), provider).delete_from_provider()
    )
    service_catalogs = ServiceCatalogs(appliance, catalog_item.catalog, catalog_item.name)
    service_catalogs.order()
    # nav to requests page happens on successful provision
    logger.info('Waiting for cfme provision request for service %s', catalog_item.name)
    request_description = catalog_item.name
    provision_request = appliance.collections.requests.instantiate(request_description,
                                                                   partial_check=True)
    provision_request.wait_for_request()
    msg = "Provisioning failed with the message {}".format(provision_request.rest.message)
    assert provision_request.is_succeeded(), msg
