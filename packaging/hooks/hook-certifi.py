"""Collect certifi's runtime CA bundle with its package-relative layout."""

from PyInstaller.utils.hooks import collect_data_files

# certifi.where() resolves ``files("certifi").joinpath("cacert.pem")`` in
# current certifi releases.  Keep the resource under the package directory so
# that importlib.resources continues to resolve it in a frozen process.
datas = collect_data_files("certifi", includes=["cacert.pem"])
