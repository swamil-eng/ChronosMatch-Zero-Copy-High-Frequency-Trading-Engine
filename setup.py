"""Optional: build the Cython matching engine.   python setup.py build_ext --inplace

Needs Cython and a C compiler (Windows: Microsoft C++ Build Tools). If the build is skipped,
ChronosMatch falls back to the pure-Python book automatically.
"""
from setuptools import Extension, setup

try:
    from Cython.Build import cythonize
    ext_modules = cythonize([Extension("chronosmatch.book_cy", ["chronosmatch/book_cy.pyx"])])
except ImportError:
    ext_modules = []

setup(name="chronosmatch", version="0.2.0", packages=["chronosmatch"], ext_modules=ext_modules)
