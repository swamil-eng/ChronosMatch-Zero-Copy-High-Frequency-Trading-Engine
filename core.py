"""Pick the fastest available order book implementation."""
import os

BACKEND = "python"
Book = None

if not os.environ.get("CHRONOS_FORCE_PYTHON"):
    try:
        from .book_cy import CBook as Book      # built with: python setup.py build_ext --inplace
        BACKEND = "cython"
    except ImportError:
        Book = None

if Book is None:
    from .book_py import PyBook as Book
