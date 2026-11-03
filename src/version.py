# The running build's version. CI overwrites this line before PyInstaller runs (see
# .github/workflows/build.yml): with the released tag, or with the tag plus "-test" for a
# manual run, which releases nothing. Only a plain vX.Y.Z turns the update check on, so
# the dev default that source runs and local builds keep silences it, as "-test" does.
__version__ = "0.0.0-dev"
