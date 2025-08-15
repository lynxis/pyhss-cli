#!/usr/bin/env python3

import logging
import re
import sys

import click
import httpx

VERSION = '0.0.1'
LOG = logging.getLogger()

def validate_imsi(_ctx, _param, value):
    if len(value) != 15:
        raise click.BadParameter("IMSI must be 15 digits long")

    valid = '0123456789'
    contains_invalid = [x for x in value if x not in valid]
    if contains_invalid:
        raise click.BadParameter(f"Contains the following invalid IMSI characters: {contains_invalid}")

    return value

def validate_hex(_ctx, _param, value):
    if len(value) % 2 != 0:
        raise click.BadParameter("Hexstrings must have even amount of digits.")

    return value

def validate_key(ctx, param, value):
    if value is None:
        return None

    value = validate_hex(ctx, param, value)

    valid = [16, 32]
    if len(value) not in valid:
        raise click.BadParameter(f"Must have 128 or 256 bit length. Given {int(len(value) / 2 * 8)} bits")

    return value

def get_headers(ctx: click.Context) -> dict:
    if ctx.obj['APIKEY']:
        return {'Provisioning-Key': ctx.obj['APIKEY']}
    return {}

CONTEXT_SETTINGS = dict(show_default=True)

@click.group(context_settings=CONTEXT_SETTINGS)
@click.option('--api', help='Url to the pyhss API.', type=str, default="http://127.0.0.1:8080", envvar='PYHSS_API')
@click.option('--api-key', help='Api key. See provisioning_key in pyHss config.toml', type=str, default="changeThisInProduction", envvar='PYHSS_APIKEY')
@click.version_option()
@click.pass_context
def cli(ctx, api, api_key):
    ctx.ensure_object(dict)
    ctx.obj['API'] = api
    ctx.obj['APIKEY'] = api_key


@cli.command()
@click.argument('imsi', type=str, callback=validate_imsi)
@click.option('--ki', help='The raw key as hexstr usualy 16 bytes as 32 characters.', type=str, callback=validate_key, required=True)
@click.option('--opc', help='The OPc key as hexstr usualy 16 bytes as 32 characters.', callback=validate_key, type=str)
@click.option('--op', help='The OP key as hexstr usualy 16 bytes as 32 characters. More common is to specify an OPc instead of a OP.', callback=validate_key, type=str)
@click.option('--sqn', default=0, help='Sequence number.', type=int)
@click.option('--iccid', help='ICCID of the subscriber', type=str)
@click.option('--msisdn', help='MSISDN of the subscriber', type=str)
@click.option('--default-apn', help='Default APN of the subscriber', type=str, required=True)
@click.option('--apn', help='Add APN to the allowed list', type=str, multiple=True)
@click.option('--remove-old-auc', help='Remove old AUC entry if already present.', is_flag=True)
@click.pass_context
def add_subscriber(ctx, imsi, ki, opc, op, sqn, iccid, msisdn, default_apn, apn, remove_old_auc):
    """ Add a subscriber to the AUC and IMSI database
        
        Before adding any data, the default apn and allowed apns will be valided
    """
    if opc and op:
        raise click.BadParameter("Can't specify both OP and OPc!")
    if not opc and not op:
        raise click.BadParameter("Require either OP or OPc!")

    with httpx.Client(headers=get_headers(ctx)) as client:
        api = ctx.obj['API']

        # get default APN
        apn_obj = get_apn(client, api, default_apn)
        if not apn_obj:
            click.echo(f"Could not find the default apn '{default_apn}'. Please add it first via add-apn.")
            sys.exit(1)

        default_apn_id = apn_obj['apn_id']

        # get apn_list
        apn_list = []
        for single_apn in apn:
            apn_obj = get_apn(client, api, single_apn)
            if apn_obj:
                apn_list.append(apn_obj['apn_id'])
            else:
                click.echo(f"Could not find the given --apn '{single_apn}'. Please add it first via add-apn.")
                sys.exit(1)
        LOG.debug("Created APN list {apn_list} (without default_apn)")

        # add subscriber to the AUC
        old_sub_entry = get_subscriber(client, api, imsi)
        if old_sub_entry:
            click.echo("Subscriber already exist in subscriber database!")
            sys.exit(1)

        auc_entry = {
            'ki': ki,
            'sqn': sqn,
            'amf': '8000', # AMF for E-UTRAN requires 0x8000, even this shouldn't be part of the subscriber record
            'imsi': imsi,
        }

        if iccid:
            auc_entry['iccid'] = iccid

        if opc:
            auc_entry['opc'] = opc

        if op:
            auc_entry['op'] = op

        old_auc_entry = get_auc(client, api, imsi)
        if old_auc_entry:
            if remove_old_auc:
                delete_auc(client, api, old_auc_entry['auc_id'])
            else:
                click.echo(f"Subscriber {imsi} already exist in AUC database! Use --remove-old-auc to override")
                sys.exit(1)

        try:
            resp = client.put(f'{api}/auc/', json=auc_entry)
            resp_obj = resp.json()
            resp.raise_for_status()
            auc_id = resp_obj['auc_id']
        except httpx.HTTPStatusError as exp:
            click.echo(f"Failed to add the subscriber {imsi} to the AUC, PyHSS responded with HTTP {exp.response.status_code} {exp.response.content}")
            sys.exit(1)

        if resp_obj is None:
            click.echo(f"Failed to add the subscriber {imsi} to the AUC, PyHSS responded with empty json")

        # Convert apn_list to str
        apn_list = [str(x) for x in apn_list]
        apn_list = ','.join(apn_list)
        subscriber_entry = {
            'auc_id': auc_id,
            'imsi': imsi,
            'enabled': True,
            'default_apn': default_apn_id,
            'roaming_enabled': True,
            'apn_list': apn_list,
        }

        if msisdn:
            subscriber_entry['msisdn'] = msisdn

        try:
            resp = client.put(f'{api}/subscriber/', json=subscriber_entry)
            sub_obj = resp.json()
            resp.raise_for_status()
        except httpx.HTTPStatusError as exp:
            click.echo(f"Failed to add the subscriber to the AUC, PyHSS responded with HTTP {exp.response.status_code} {exp.response.content}")
            sys.exit(1)

        click.echo(f'Subscriber {imsi} added as subscriber id {sub_obj["subscriber_id"]}')

@cli.command()
@click.argument('imsi', type=str, callback=validate_imsi)
@click.pass_context
def remove_subscriber(ctx, imsi):
    with httpx.Client(headers=get_headers(ctx)) as client:
        api = ctx.obj['API']

        subscriber_obj = get_subscriber(client, api, imsi)
        if not subscriber_obj:
            click.echo(f"Couldn't find subscriber {imsi}. Does not exist!")
            sys.exit(1)

        try:
            resp = client.delete(f'{api}/subscriber/{subscriber_obj["subscriber_id"]}')
            result = resp.json()
            resp.raise_for_status()
        except httpx.HTTPStatusError as exp:
            click.echo(f"Failed to remove subscriber {imsi}, PyHSS responded with HTTP {exp.response.status_code}. {exp.response.content}")
            sys.exit(1)
        LOG.debug("Removing subscriber returned %s", result)

    if failed_result(result):
        raise RuntimeError(f"Couldn't delete subscriber {imsi} / id {subscriber_obj['subscriber_id']}")

def get_subscriber(client, api, imsi) -> dict | None:
    try:
        resp = client.get(f'{api}/subscriber/imsi/{imsi}')
        resp.raise_for_status()
        sub_obj = resp.json()
    except httpx.HTTPStatusError as exp:
        if exp.response.status_code == 404:
            return None
        click.echo(f"Failed to get the subscriber {imsi} from Subscriber, PyHSS responded with HTTP {exp.response.status_code}. {exp.response.content}")
        raise

    return sub_obj

def get_ims_subscriber(client, api, imsi=None, msisdn=None) -> dict | None:
    try:
        if imsi:
            resp = client.get(f'{api}/ims_subscriber/ims_subscriber_imsi/{imsi}')
        elif msisdn:
            resp = client.get(f'{api}/ims_subscriber/ims_subscriber_msisdn/{msisdn}')
        else:
            return None

        resp.raise_for_status()
        sub_obj = resp.json()
    except httpx.HTTPStatusError as exp:
        if exp.response.status_code == 404:
            return None
        click.echo(f"Failed to get the subscriber {imsi} from Subscriber, PyHSS responded with HTTP {exp.response.status_code}. {exp.response.content}")
        raise

    return sub_obj

def get_auc(client, api, imsi) -> dict | None:
    try:
        resp = client.get(f'{api}/auc/imsi/{imsi}')
        resp.raise_for_status()
        auc_obj = resp.json()
    except httpx.HTTPStatusError as exp:
        if exp.response.status_code == 404:
            return None
        click.echo(f"Failed to get the subscriber {imsi} from AUC, PyHSS responded with HTTP {exp.response.status_code}. {exp.response.content}")
        raise

    return auc_obj

def failed_result(result):
    if not isinstance(result, dict):
        return True

    if 'Result' not in result or result['Result'] != 'OK':
        return True
    return False

def delete_auc(client: httpx.Client, api: str, auc_id: str) -> dict | None:
    try:
        resp = client.delete(f'{api}/auc/{auc_id}')
        resp.raise_for_status()
        result = resp.json()
    except httpx.HTTPStatusError as exp:
        click.echo(f"Failed to remove AUC entry id {auc_id} from AUC, PyHSS responded with HTTP {exp.response.status_code}. {exp.response.content}")
        raise

    if failed_result(result):
        raise RuntimeError(f"Couldn't delete auc entry id {auc_id}. PyHSS responded {result}")

def get_apn(client: httpx.Client, api: str, apn: str) -> dict | None:
    apns = []
    try:
        resp = client.get(f'{api}/apn/list')
        resp.raise_for_status()
        apns = resp.json()
    except httpx.HTTPStatusError as exp:
        click.echo(f"Failed to get the list of APNs, PyHSS responded with HTTP {exp.response.status_code}. {exp.response.content}")
        raise

    results = list(filter(lambda entry: entry['apn'] == apn, apns))
    if results:
        return results[0]

    # return apn
    return None

_CONVERT_BIT_RE = re.compile(r'[ ]*(?P<bw>[0-9]+)[ ]?(?P<unit>[kmg]?bit)?')
def convert_mbit(bandwidth: str) -> int:
    """ bandwidth maybe '100mbit' or 100 or 1gbit or '1 gbit' """
    bits = {
        'bit': 0,
        'kbit': 3,
        'mbit': 6,
        'gbit': 9,
    }

    bandwidth = bandwidth.lower()
    match = _CONVERT_BIT_RE.match(bandwidth)
    if not match:
        raise ValueError("Input doesn't match bandwidth string. E.g. '100mbit'")

    value, unit = match.groups()
    if unit:
        expo = bits[unit]
    else:
        expo = 0

    return int(value) * (10 ** expo)

@cli.command()
@click.argument('apn', type=str)
@click.option('--dl', default='150mbit', help='The maximum APN bandwidth downlink (towards phone) (AMBR DL) ', type=str)
@click.option('--ul', default='50mbit', help='The maximum APN bandwidth uplink (towards network) (AMBR UL) ', type=str)
@click.option('--qci', default=9, help='QCI value', type=int)
@click.option('--arp', default=9, help='ARP priority', type=int)
@click.option('--preemption-cap', default=False, help='APN has (ARP) preemption capability. It can preempt other PDNs to get more bandwidth.', type=bool)
@click.option('--preemption-vuln', default=True, help='APN is (ARP) preemption vulnerable. It can be preemted and other PDNs will get more bandwdith.', type=bool)
@click.pass_context
def add_apn(ctx, apn, dl, ul, qci, arp, preemption_cap, preemption_vuln):
    # try to find the apn with the name

    with httpx.Client(headers=get_headers(ctx)) as client:
        api = ctx.obj['API']

        apn_obj = get_apn(client, api, apn)
        if apn_obj:
            LOG.debug("Found apn entry %s.", apn_obj)
            click.echo(f"APN {apn} already exists (apn_id: {apn_obj['apn_id']})!")
            sys.exit(1)

        apn_obj = {
            'apn': apn,
            'apn_ambr_dl': convert_mbit(dl),
            'apn_ambr_ul': convert_mbit(ul),
            'qci': qci,
            'arp_priority': arp,
            'arp_preemption_vulnerability': preemption_vuln,
            'arp_preemption_capability': preemption_cap,
        }

        try:
            resp = client.put(f'{api}/apn/', json=apn_obj)
            apn_obj = resp.json()
            resp.raise_for_status()
        except httpx.HTTPStatusError as exp:
            click.echo(f"Failed to add the APN {apn}, PyHSS responded with HTTP {exp.response.status_code}")
            sys.exit(1)

        LOG.debug("APN added: %s", apn_obj)
        click.echo(f"APN {apn} added under id: {apn_obj['apn_id']}")

@cli.command()
@click.argument('apn', type=str)
@click.pass_context
def remove_apn(ctx, apn):
    with httpx.Client(headers=get_headers(ctx)) as client:
        api = ctx.obj['API']

        apn_obj = get_apn(client, api, apn)
        if not apn_obj:
            click.echo(f"Couldn't find apn {apn}. Does not exist!")
            sys.exit(1)

        try:
            resp = client.delete(f'{api}/apn/{apn_obj["apn_id"]}')
            resp_obj = resp.json()
            resp.raise_for_status()
        except httpx.HTTPStatusError as exp:
            click.echo(f"Failed to remove apn {apn}, PyHSS responded with HTTP {exp.response.status_code}. {exp.response.content}")
            sys.exit(1)
        LOG.debug("Removing APN returned %s", resp_obj)

@cli.command()
@click.option('--imsi', 'imsi', help='Show only a single subscriber.')
@click.option('-l', 'display', flag_value='long', help='Long output, show all fields.')
@click.option('-b', 'display', flag_value='brief', help='brief output, show AMBR, MSISDN, enabled, roaming_enabled, default_apn')
@click.option('-i', 'display', flag_value='imsi', help='Show only the imsi of a subscriber')
@click.option('--limit', help='Limit output of subscribers', default=100, type=int)
@click.option('--page', help='Page through subscribers', default=0, type=int)
@click.pass_context
def list_subscribers(ctx, imsi, display, page, limit):
    """ list subscribers

        The brief output shows AMBR, MSISDN, enabled, roaming_enabled, default_apn.
        The long output shows all properties.
        The imsi output only shows only a single line
    """

    with httpx.Client(headers=get_headers(ctx)) as client:
        api = ctx.obj['API']

        if imsi:
            subscriber = get_subscriber(client, api, imsi)
            if not subscriber:
                click.echo(f"Couldn't find subscriber {imsi}")
                sys.exit(1)
            subscribers = [subscriber]
        else:
            try:
                resp = client.get(f'{api}/subscriber/list', params=dict(page_size=limit, page=page))
                resp.raise_for_status()
                subscribers = resp.json()
            except httpx.HTTPStatusError as exp:
                click.echo(f"Failed to list subscribers, PyHSS responded with HTTP {exp.response.status_code}. {exp.response.content}")
                raise

    # TODO: sorting of fields
    # TODO: resolve default apn and apn list
    # TODO: cache apn list for resolving
    brief_fields = [
        'ue_ambr_dl',
        'ue_ambr_ul',
        'default_apn',
        'msisdn',
        'enabled',
        'roaming_enabled',
    ]

    # No display option is selected
    if not display:
        if imsi:
            display = 'brief'
        else:
            display = 'imsi'

    if display == 'long':
        for sub in subscribers:
            for field in sub:
                if field == 'imsi':
                    continue

                click.echo(f"{sub['imsi']}, {field}: {sub[field]}")

    elif display == 'brief':
        for sub in subscribers:
            for field in brief_fields:
                click.echo(f"{sub['imsi']}, {field}: {sub[field]}")

    elif display == 'imsi':
        for sub in subscribers:
            click.echo(f"{sub['imsi']}")


@cli.command()
@click.option('--apn', 'apn', help='Show only this APN.')
@click.option('-l', 'display', flag_value='long', help='Long output, show all fields.')
@click.option('-b', 'display', flag_value='brief', help='brief output, show AMBR, QCI, ARP.')
@click.option('-i', 'display', flag_value='id', help='Show only the id of the APN')
@click.pass_context
def list_apns(ctx, apn, display):
    """ list all APNs

        The brief output shows AMBR, QCI, ARP.
        The long output shows all properties.
        The id output only shows only a single line
    """

    with httpx.Client(headers=get_headers(ctx)) as client:
        api = ctx.obj['API']

        if apn:
            apns = get_apn(client, api, apn)
            if not apns:
                click.echo(f"Couldn't find APN {apn}")
                sys.exit(1)
            apns = [apns]
        else:
            try:
                resp = client.get(f'{api}/apn/list')
                resp.raise_for_status()
                apns = resp.json()
            except httpx.HTTPStatusError as exp:
                click.echo(f"Failed to list apns, PyHSS responded with HTTP {exp.response.status_code}. {exp.response.content}")
                raise

    brief_fields = [
        'apn_id',
        'apn_ambr_dl',
        'apn_ambr_ul',
        'arp_preemption_capability',
        'arp_preemption_vulnerability',
        'arp_priority',
        'qci',
    ]

    # No display option is selected
    if not display:
        if apn:
            display = 'brief'
        else:
            display = 'id'

    if display == 'long':
        for apn in apns:
            for field in apn:
                if field == 'apn':
                    continue

                click.echo(f"{apn['apn']}, {field}: {apn[field]}")
    elif display == 'brief':
        for apn in apns:
            for field in brief_fields:
                click.echo(f"{apn['apn']}, {field}: {apn[field]}")
    elif display == 'id':
        for apn in apns:
            click.echo(f"{apn['apn']}: id {apn['apn_id']}")

@cli.command()
@click.argument('imsi', type=str)
@click.option('--msisdn', help='MSISDN (first is the primary)', multiple=True, required=True, type=str)
@click.option('--icf', help='ICF (Initial Filter Criteria) path to the xml on the HSS', type=str)
@click.pass_context
def add_ims_subscriber(ctx, imsi, msisdn, icf):

    with httpx.Client(headers=get_headers(ctx)) as client:
        api = ctx.obj['API']

        sub_obj = get_subscriber(client, api, imsi)
        if not sub_obj:
            click.echo(f"Couldn't find the subscriber {imsi} in the subscriber DB! Please add the subscriber with `add-subscriber`")
            sys.exit(1)

        primary_msisdn = msisdn[0]
        additional = list(msisdn)[1:]
        additional = [str(x) for x in additional]
        additional = ','.join(additional)

        ims_obj = {
            'imsi': imsi,
            'msisdn': primary_msisdn,
            'msisdn_list': additional,
        }

        try:
            resp = client.put(f'{api}/ims_subscriber/', json=ims_obj)
            subscriber_obj = resp.json()
            resp.raise_for_status()
        except httpx.HTTPStatusError as exp:
            click.echo(f"Failed to add the IMS subscriber {imsi}, PyHSS responded with HTTP {exp.response.status_code} {exp.response.content}")
            sys.exit(1)

        LOG.debug("IMS Subscriber added: %s", subscriber_obj)
        click.echo(f"IMS subscriber {imsi} added under id: {subscriber_obj['ims_subscriber_id']}")

@cli.command()
@click.argument('imsi', type=str)
@click.pass_context
def remove_ims_subscriber(ctx, imsi):
    with httpx.Client(headers=get_headers(ctx)) as client:
        api = ctx.obj['API']

        ims_obj = get_ims_subscriber(client, api, imsi)
        if not ims_obj:
            click.echo(f"Couldn't find IMS subscriber {imsi}. Does not exist!")
            sys.exit(1)
        print(f"Found subscriber {ims_obj}")

        try:
            resp = client.delete(f'{api}/ims_subscriber/{ims_obj["ims_subscriber_id"]}')
            result = resp.json()
            resp.raise_for_status()
        except httpx.HTTPStatusError as exp:
            click.echo(f"Failed to remove IMS subscriber {imsi}, PyHSS responded with HTTP {exp.response.status_code}. {exp.response.content}")
            sys.exit(1)

    LOG.debug("Removing ims returned %s", result)
    if failed_result(result):
        raise RuntimeError(f"Couldn't delete IMS subscriber {imsi} / id {ims_obj['ims_subscriber_id']}")

@cli.command()
@click.option('--imsi', 'imsi', help='Show only a single subscriber by IMSI.')
@click.option('--msisdn', 'msisdn', help='Show only a single subscriber by MSISDN.')
@click.option('-l', 'display', flag_value='long', help='Long output, show all fields.')
@click.option('-b', 'display', flag_value='brief', help='brief output, show AMBR, QCI, ARP.')
@click.option('-i', 'display', flag_value='imsi', help='Show only the imsi of a subscriber')
@click.option('--limit', help='Limit output of subscribers', default=100, type=int)
@click.option('--page', help='Page through subscribers', default=0, type=int)
@click.pass_context
def list_ims_subscribers(ctx, imsi, msisdn, display, page, limit):
    """ list ims subscribers

        The brief output shows AMBR, QCI, ARP.
        The long output shows all properties.
        The id output only shows only a single line
    """

    if imsi and msisdn:
        click.echo("Can't use both --imsi and --msisdn to filter for an IMS subscriber.")
        sys.exit(1)

    with httpx.Client(headers=get_headers(ctx)) as client:
        api = ctx.obj['API']

        if imsi:
            subscriber = get_ims_subscriber(client, api, imsi=imsi)
            if not subscriber:
                click.echo(f"Couldn't find subscriber by IMSI {imsi}")
                sys.exit(1)
            subscribers = [subscriber]
        elif msisdn:
            subscriber = get_ims_subscriber(client, api, msisdn=msisdn)
            if not subscriber:
                click.echo(f"Couldn't find subscriber by MSISDN {msisdn}")
                sys.exit(1)
            subscribers = [subscriber]
        else:
            try:
                resp = client.get(f'{api}/ims_subscriber/list', params=dict(page_size=limit, page=page))
                resp.raise_for_status()
                subscribers = resp.json()
            except httpx.HTTPStatusError as exp:
                click.echo(f"Failed to list IMS subscribers, PyHSS responded with HTTP {exp.response.status_code}. {exp.response.content}")
                raise

    # TODO: sorting of fields
    brief_fields = [
        'imsi',
        'msisdn',
        'msisdn_list',
        'msisdn',
        'pcscf',
        'scscf',
        'scscf_timestamp',
    ]

    # No display option is selected
    if not display:
        if imsi:
            display = 'brief'
        else:
            display = 'imsi'

    if display == 'long':
        for sub in subscribers:
            for field in sub:
                if field == 'imsi':
                    continue

                click.echo(f"{sub['imsi']}, {field}: {sub[field]}")

    elif display == 'brief':
        for sub in subscribers:
            for field in brief_fields:
                click.echo(f"{sub['imsi']}, {field}: {sub[field]}")

    elif display == 'imsi':
        for sub in subscribers:
            click.echo(f"{sub['imsi']}")
