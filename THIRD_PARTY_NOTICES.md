# Third-party notices

`dlsite-organizer` is distributed under the MIT License in `LICENSE`. The
third-party components listed below remain under their own licenses. This file
is included with the Windows distribution so that the notices travel with the
standalone application.

The versions in the final Windows artifact are the versions installed in the
canonical `.venv` at build time. The dependency ranges below are the runtime
requirements declared by this project.

| Component | Declared runtime requirement | License / notice | Project and license source |
| --- | --- | --- | --- |
| PySide6 Essentials / Qt for Python | `PySide6-Essentials>=6.8,<7` | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only; commercial licensing is also offered by The Qt Company | [Qt for Python](https://doc.qt.io/qtforpython/) · [Qt licensing](https://www.qt.io/licensing/) |
| shiboken6 | selected by PySide6 Essentials | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only; commercial licensing is also offered by The Qt Company | [Qt for Python](https://doc.qt.io/qtforpython/) · [Qt licensing](https://www.qt.io/licensing/) |
| SQLAlchemy | `SQLAlchemy>=2.0,<3` | MIT | [SQLAlchemy](https://www.sqlalchemy.org/) · [license](https://github.com/sqlalchemy/sqlalchemy/blob/main/LICENSE) |
| selectolax | `selectolax>=0.3.27,<1` | MIT | [selectolax](https://github.com/rushter/selectolax) · [license](https://github.com/rushter/selectolax/blob/main/LICENSE) |
| HTTPX | `httpx>=0.28,<1` | BSD-3-Clause | [HTTPX](https://www.python-httpx.org/) · [license](https://github.com/encode/httpx/blob/master/LICENSE.md) |
| httpcore | selected by HTTPX | BSD-3-Clause | [httpcore](https://www.encode.io/httpcore/) · [license](https://github.com/encode/httpcore/blob/master/LICENSE.md) |
| certifi | selected by HTTPX/httpcore | Mozilla Public License 2.0 | [certifi](https://github.com/certifi/python-certifi) · [license](https://github.com/certifi/python-certifi/blob/master/LICENSE) |
| idna | selected by HTTPX/httpcore | BSD-3-Clause | [idna](https://github.com/kjd/idna) · [license](https://github.com/kjd/idna/blob/master/LICENSE.md) |
| anyio | selected by HTTPX/httpcore | MIT | [AnyIO](https://anyio.readthedocs.io/) · [license](https://github.com/agronholm/anyio/blob/master/LICENSE) |
| h11 | selected by HTTPX/httpcore | MIT | [h11](https://github.com/python-hyper/h11) · [license](https://github.com/python-hyper/h11/blob/master/LICENSE.txt) |
| Pydantic | `pydantic>=2.10,<3` | MIT | [Pydantic](https://github.com/pydantic/pydantic) · [license](https://github.com/pydantic/pydantic/blob/main/LICENSE) |
| pydantic-core | selected by Pydantic | MIT | [pydantic-core](https://github.com/pydantic/pydantic-core) · [license](https://github.com/pydantic/pydantic-core/blob/main/LICENSE) |
| greenlet | selected by SQLAlchemy | MIT; PSF-2.0 applies to Stackless-derived files | [greenlet](https://github.com/python-greenlet/greenlet) · [license](https://github.com/python-greenlet/greenlet/blob/main/LICENSE) |
| annotated-types | selected by Pydantic | MIT | [annotated-types](https://github.com/annotated-types/annotated-types) · [license](https://github.com/annotated-types/annotated-types/blob/main/LICENSE) |
| typing-extensions | selected by Pydantic | Python Software Foundation License / 2-clause BSD License | [typing-extensions](https://github.com/python/typing_extensions) · [license](https://github.com/python/typing_extensions/blob/main/LICENSE) |
| typing-inspection | selected by Pydantic | MIT | [typing-inspection](https://github.com/pydantic/typing-inspection) · [license](https://github.com/pydantic/typing-inspection/blob/main/LICENSE) |

## Distribution note

PySide6 Essentials and shiboken6 are published by the Qt for Python project
with open-source and commercial licensing options. This project does not
relicense Qt or provide a commercial Qt license. The PySide6/shiboken6 terms
and the license selected by the distributor continue to apply to those
components.

PyInstaller is a build-time tool and is not bundled as an application runtime
dependency. Its license is therefore not part of the application runtime
notice set.
