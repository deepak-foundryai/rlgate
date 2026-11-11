"""Workspaces copied out of a container keep their absolute symlinks (venvs have them),
but nothing is ever written through a link that points outside the copy."""

import io
import os
import tarfile

import pytest

from rlgate.sandbox.machine import _keep_links


def _tar(members):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, link, data in members:
            ti = tarfile.TarInfo(name)
            if link:
                ti.type, ti.linkname = tarfile.SYMTYPE, link
                tf.addfile(ti)
            else:
                ti.size = len(data)
                tf.addfile(ti, io.BytesIO(data))
    buf.seek(0)
    return tarfile.open(fileobj=buf, mode="r:gz")


def test_absolute_symlink_is_kept(tmp_path):
    with _tar([("./venv/bin/python", "/usr/bin/python3", None),
               ("./venv/lib/x.py", None, b"x")]) as tf:
        tf.extractall(tmp_path, filter=_keep_links)
    assert os.readlink(tmp_path / "venv/bin/python") == "/usr/bin/python3"


def test_no_write_through_an_outside_link(tmp_path):
    with _tar([("./out", str(tmp_path.parent), None), ("./out/evil", None, b"x")]) as tf, \
            pytest.raises(tarfile.OutsideDestinationError):
        tf.extractall(tmp_path / "dest", filter=_keep_links)
    assert not (tmp_path.parent / "evil").exists()
