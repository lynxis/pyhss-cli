# pyhss-cli - a cli for PyHSS using REST

pyhss-cli is a command line interface to interact with PyHSS using
the REST interface.

PyHSS is a LTE HSS and PCRF. For further details see the [PyHSS](https://github.com/nickvsnetworking/pyhss).

Please use `pyhss-cli --help` for all available commands.

## Overview of supported commands

- add/list/remove subscribers
- add/list/remove APNs

# Basic Usage

```
# Install pyhss
poetry install
pyhss --version

# Or use it via poetry run
poetry run pyhss --version

# If your PyHSS is available via a different endpoint than 127.0.0.1:8080
pyhss --api http://pyhss.fe80.eu:8080 list-subscribers

# The environment PYHSS_API and PYHSS_APIKEY can be used instead of --api --api-key
PYHSS_API=http://pyhss.fe80.eu:8080 pyhss list-subscribers

# Add common APNs
pyhss add-apn internet
pyhss add-apn ims
pyhss add-apn mms

# Add a subscriber (the default apn must be set)
pyhss add-subscriber 999420000000012 --ki 1234556780abcdef --opc efeeffffffffffff --default-apn internet

# Add a subscriber and allow APNs ims, mms
pyhss add-subscriber 999420000000013 \
  --ki 1234556780abcdff --opc efeeffffffffffee \
  --msisdn 03090013 \
  --default-apn internet --apn ims --apn mms

```

## TODO

- subscriber: add support to update fields
- apn: add support to update fields
- ims: add support to add/remove/list/set IMS related objects
- auc: add/remove/list/set of the AUC object

