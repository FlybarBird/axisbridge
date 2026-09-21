"""Also probes OpenWrt's split Python standard-library packages."""
import sys
if sys.version_info < (3, 10):
    print('Python 3.10+ required; found ' + sys.version.split()[0])
    sys.exit(1)
try:
    import argparse, collections, dataclasses, hashlib, hmac, http.server
    import http.cookies, ipaddress, json, math, mimetypes, os, pathlib
    import re, secrets, select, shutil, signal, socket, struct, subprocess
    import tempfile, threading, time, urllib.request, webbrowser
except ImportError as exc:
    print('Incomplete Python standard library: ' + str(exc))
    sys.exit(1)
print('Python runtime OK: ' + sys.version.split()[0])
