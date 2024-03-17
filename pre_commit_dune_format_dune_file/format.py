#!/usr/bin/env python3

import argparse
import logging
import os
import subprocess
import sys
import tempfile
import shutil
import functools
from typing import Optional, Final

from path import Path, TempDir
from rich.console import Console
from rich.logging import RichHandler

from pre_commit_dune_format_dune_file._version import (
    __version__ as pre_commit_dune_format_dune_file_version,
)

LOG_FORMAT = "%(message)s"
logging.basicConfig(
    level=logging.WARNING,
    format=LOG_FORMAT,
    datefmt="[%X]",
    handlers=[RichHandler(console=Console(stderr=True), rich_tracebacks=True)],
)

program_name: Final[str] = "pre-commit-dune-format-dune-file"

log = logging.getLogger(program_name)


@functools.cache
def dune_get_exe() -> Path:
    p = shutil.which("dune")
    if p is None:
        raise FileNotFoundError(
            "'dune' executable not found. Try running 'opam install dune'."
        )
    return Path(p)


_dune_file_names: Final[tuple[Path, Path]] = (
    Path("dune"),
    Path("dune-project"),
    Path("dune-workspace"),
)


def get_dune_file_path(arg: str) -> Optional[Path]:
    path = Path(arg)
    if path.isfile() and path.name in _dune_file_names:
        if not path.access(os.R_OK):
            raise PermissionError(f"Can't read dune file '{path}'")
        if not path.access(os.W_OK):
            raise PermissionError(f"Can't write dune file '{path}'")
        return path
    else:
        return None


class SaveableTempDir(TempDir):
    @staticmethod
    def __super_kwargs__(**kwargs):
        my_kwargs = ("save",)
        super_kwargs = {k: v for k, v in kwargs.items() if k not in my_kwargs}
        return super_kwargs

    def __new__(cls, *args, **kwargs):
        return super().__new__(cls, *args, **cls.__super_kwargs__(**kwargs))

    def __init__(self, *args, save=False, **kwargs) -> None:
        super().__init__(*args, **self.__super_kwargs__(**kwargs))
        self._save = save

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if not self._save:
            super().__exit__(exc_type, exc_val, exc_tb)


def real_main(args: argparse.Namespace) -> int:
    if args.version:
        print(f"{program_name} version: {pre_commit_dune_format_dune_file_version}")
        print(
            "dune format-dune-file version: "
            + subprocess.run(
                (dune_get_exe(), "format-dune-file", "--version"),
                capture_output=True,
                text=True,
            ).stdout,
            end="",
        )
        return 0
    verbose: Final[bool] = args.verbose_hook
    if verbose:
        log.setLevel(logging.INFO)
        log.info(f"{program_name}: verbose-hook mode enabled")
    save_temps: Final[bool] = args.save_dune_temps
    in_place: Final[bool] = args.in_place
    if in_place:
        log.info(f"{program_name} operating in in-place mode")
    else:
        log.info(f"{program_name} will output formatted code to stdout")
    with SaveableTempDir(prefix=f"{program_name}-tmp-", save=save_temps) as d:
        if save_temps or verbose:
            log.warning(f"Saving {program_name} temporary files in '{d}'")
            if save_temps:
                log.info(f"{program_name} will not delete the directory on exit")
        orig_dune_to_tmp_dune: dict[Path, Optional[Path]] = {}
        for should_be_dune_file in args.dune_files:
            orig_dune_file = get_dune_file_path(should_be_dune_file)
            if orig_dune_file is None:
                raise TypeError(
                    f"{should_be_dune_file} should be named either 'dune', 'dune-project' or 'dune-workspace'."
                )
            log.info(f"Found a dune or dune-project file to format: '{orig_dune_file}'")
            if in_place:
                # create a temporary dune file for formatting to avoid partial failure
                this_tmp_dune_file = Path(
                    tempfile.NamedTemporaryFile(
                        mode="w",
                        dir=d,
                        prefix=orig_dune_file.name + "-formatted-",
                        delete=False,
                    ).name
                )
                orig_dune_to_tmp_dune[orig_dune_file] = this_tmp_dune_file
            else:
                orig_dune_to_tmp_dune[orig_dune_file] = None
        # run dune format-dune-file
        for orig_dune_file, tmp_dune_file in orig_dune_to_tmp_dune.items():
            df_cmd = (dune_get_exe(), "format-dune-file", orig_dune_file)
            log.info(f"{program_name} is running '{' '.join(df_cmd)}'")
            try:
                df_res = subprocess.run(
                    df_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    check=True,
                )
            except subprocess.CalledProcessError as e:
                log.error(f"dune format-dune-file error output:\n{e.stdout}")
                log.exception(
                    f"{program_name} got return code {e.returncode} while running '{' '.join(df_cmd)}'"
                )
                return e.returncode
            except Exception:
                log.error(f"dune format-dune-file error output:\n{df_res.stdout}")
                log.exception(
                    f"Received an unexpected exception when running '{' '.join(df_cmd)}'"
                )
                return 1
            if in_place:
                if tmp_dune_file is None:
                    raise FileNotFoundError(
                        f"{program_name} can't find temporary formatted dune file for original '{orig_dune_file}'."
                    )
                log.info(
                    f"{program_name} writing formatted '{orig_dune_file}' to temporary '{tmp_dune_file}'"
                )
                with open(tmp_dune_file, "w") as f:
                    f.write(df_res.stdout)
                with open(tmp_dune_file) as f:
                    log.info(f"wrote out:\n{f.read()}")
            else:
                print(df_res.stdout, end="")
        if in_place:
            log.info(
                f"{program_name} formatting was successful. Writing formatted temp files over original files."
            )
            # Copy changes from temporary formatted dune file to original dune file
            for orig_dune_file, tmp_dune_file in orig_dune_to_tmp_dune.items():
                log.info(
                    f"Overwriting '{orig_dune_file}' with formatted content from '{tmp_dune_file}'"
                )
                if tmp_dune_file is None:
                    raise FileNotFoundError(
                        f"{program_name} can't find temporary formatted dune file for original '{orig_dune_file}'."
                    )
                with open(tmp_dune_file) as tl, open(orig_dune_file, "w") as ol:
                    ol.write(tl.read())
    return 0


def get_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=program_name)
    parser.add_argument(
        "--verbose-pre-commit-dune-format-dune-file",
        action="store_true",
        dest="verbose_hook",
        help=f"Verbose output from {program_name}.",
    )
    parser.add_argument(
        "--save-dune-temps",
        action="store_true",
        help="Don't delete temporary formatted dune files.",
    )
    parser.add_argument(
        "-i",
        action="store_true",
        dest="in_place",
        help="Inplace edit <file>s, if specified.",
    )
    parser.add_argument(
        "--version", action="store_true", help="Display the version of this program"
    )
    parser.add_argument("dune_files", metavar="<DUNE FILE>", type=Path, nargs="+")
    return parser


def main() -> int:
    try:
        arg_parser = get_arg_parser()
        args = arg_parser.parse_args()
        return real_main(args)
    except Exception:
        log.exception(f"Received an unexpected exception when running {program_name}.")
        return 1
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
