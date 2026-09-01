"""Collect only certifi's runtime CA bundle for the frozen application."""

from certifi import where

datas = [(where(), ".")]
