# dimclient

`dimclient` is a Python client library to talk to DIM. It provides a method
interface to all DIM functions.

## usage

To use `dimclient`, install it in your Python environment.

There are two options to do that:

1. *Distribution packages:* Download and install a distribution-packaged package. There are packages for Linux distributions available at [openSUSE Build Service - network:dim/dimclient](https://build.opensuse.org/package/show/network:dim/dimclient).
   Note that distribution packages install the dimclient globally.
2. *Python PIP:* [PyPI dimclient](https://pypi.org/project/dimclient/)

```sh
pip install dimclient
```

In your script, create a new DIM client and log into the instance.
After that, you can make any request.

```python
import dimclient

client = dimclient.DimClient("http://localhost:8080")
# optional login
client.login('username', 'password')

# now request all IPs
ips = client.ip_list(pool="testpool")
```
