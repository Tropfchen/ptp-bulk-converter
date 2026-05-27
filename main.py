#!/usr/bin/env python3
from __future__ import annotations

import configparser
import os
import re
import shutil
import subprocess
import sys
import winreg
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

PMDG_PRODUCTS_PATTERNS: dict[str, re.Pattern] = {
    "PMDG 737 NGXu": re.compile(r"^737-(?:\S+NGXu|BBJ)\b"),
    "PMDG 737 NG": re.compile(r"^737-\S+NG\b"),
    "PMDG 777X": re.compile(r"\b777"),
    "PMDG 747 QOTS II": re.compile(r"\b747\b"),
}

COMMENT_PREFIXES = ("#", "//", ";")

AIRPLANES_DIR = Path("SimObjects/Airplanes/")

HKEY_CURRENT_USER_REG_KEYS = {
    "fsx": r"Software\Microsoft\Microsoft Games\Flight Simulator\10.0",
    "fsxse": r"Software\Microsoft\Microsoft Games\Flight Simulator - Steam Edition\10.0",
    "p3d3": r"SOFTWARE\Lockheed Martin\Prepar3D v3",
    "p3d4": r"SOFTWARE\Lockheed Martin\Prepar3D v4",
    "p3d5": r"SOFTWARE\Lockheed Martin\Prepar3D v5",
    "p3d6": r"SOFTWARE\Lockheed Martin\Prepar3D v6",
}


@dataclass(frozen=True)
class AircraftConfig:
    sim: str
    # ui_type: str
    pmdg_product: str
    relative_path: str

    # TODO: compare by pmdg_product+sim

    @classmethod
    def _extract_pmdg_configs(cls, text: str):
        cfg = configparser.ConfigParser(comment_prefixes=COMMENT_PREFIXES)
        cfg.read_string(text)

        config = None
        matched = []
        ui_type = ""
        for config in [cfg[x] for x in cfg.sections() if x.startswith("fltsim.")]:
            try:
                ui_type = config["ui_type"]
                for name, pattern in PMDG_PRODUCTS_PATTERNS.items():
                    sim = config["sim"]
                    if pattern.match(ui_type) and sim not in matched:
                        matched.append(sim)
                        yield (config, name)
                        break
            except KeyError:
                pass
        if not matched:
            raise ValueError(f"Unknown PMDG product for ui_type: {ui_type!r}")  # TODO: add request for filling issue

    @classmethod
    def from_aircraft_config_text(cls, text: str, path) -> Iterable["AircraftConfig"]:
        for section, pmdg_product in cls._extract_pmdg_configs(text):
            yield cls(
                sim=section["sim"],
                # ui_type=section["ui_type"],
                pmdg_product=pmdg_product,
                relative_path=path,
            )

    # TODO: def get_simobject_path(self)?


@dataclass(frozen=True)
class AircraftLivery(AircraftConfig):
    title: str
    ui_variation: str
    atc_id: str

    def as_aircraft_config(self) -> AircraftConfig:
        return AircraftConfig(
            sim=self.sim,
            # ui_type=self.ui_type,
            pmdg_product=self.pmdg_product,
            relative_path=self.relative_path,
        )

    @classmethod
    def from_aircraft_config_text(cls, text: str, path) -> Iterable["AircraftLivery"]:
        for section, pmdg_product in cls._extract_pmdg_configs(text):
            yield cls(
                sim=section["sim"],
                # ui_type=section["ui_type"],
                pmdg_product=pmdg_product,
                relative_path=path,
                title=section["title"],
                ui_variation=section["ui_variation"],
                atc_id=section["atc_id"],
            )


def discover_aircraft_in_sim(game_path: str | Path, ignore_unknown_ac: bool = True) -> Iterable[AircraftConfig]:
    simobjects = Path(game_path).resolve() / AIRPLANES_DIR
    for sub in simobjects.glob("PMDG*"):
        if not sub.is_dir():
            continue

        cfg = sub / "Aircraft.cfg"
        if not cfg.exists():
            continue

        try:
            text = cfg.read_text(encoding="utf-8", errors="ignore")
            rel_path = sub.name + "/"
            yield from AircraftConfig.from_aircraft_config_text(text, rel_path)
        except ValueError:
            if not ignore_unknown_ac:
                raise


def query_registry() -> Iterable[tuple[str, Path]]:
    for game, key_name in HKEY_CURRENT_USER_REG_KEYS.items():
        key = None
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_name, 0, winreg.KEY_READ)
            game_path, _ = winreg.QueryValueEx(key, "AppPath")
            # game_installed, _ = winreg.QueryValueEx(key, "Installed")
            if game_path:
                yield (game, game_path)
        except FileNotFoundError:
            continue  # expected for missing sims
        except OSError as e:
            print(f"Unexpected registry error for {game}: {e}", file=sys.stderr)
        finally:
            if key:
                winreg.CloseKey(key)


def get_games_paths() -> dict[str, Path]:
    games = {}
    for game, game_path in query_registry():
        game_path = Path(game_path)
        if (game_path / "PMDG").exists():
            games[game] = game_path
    return games


def move_texture_dirs(src_folder: Path, airplane_dir: Path, dry_run: bool) -> None:
    if not src_folder.is_dir():
        print(f"Warning: Texture source folder not found: {src_folder}")
        return
    for entry in src_folder.iterdir():
        if entry.is_dir() and entry.name.lower().startswith("texture"):
            dest = airplane_dir / entry.name
            if dest.exists():
                print(f"Recursively deleting '{dest}")
                if not dry_run:
                    shutil.rmtree(dest)
            print(f"Moving '{entry}' to '{dest}\n")
            if not dry_run:
                shutil.move(entry, dest)


def get_next_fltsim_section_index(aircraft_cfg_path: Path) -> int:
    config = configparser.ConfigParser(comment_prefixes=COMMENT_PREFIXES)
    config.read(aircraft_cfg_path)

    indices = []
    for section in config.sections():
        if section.startswith("fltsim."):
            match = re.search(r"\d+", section)
            if match:
                indices.append(int(match.group()))
    return max(indices) + 1 if indices else 0


def find_matching_model(livery: AircraftLivery, models: list[AircraftConfig]) -> AircraftConfig:
    """Find the aircraft model in sim that matches the livery's sim and product."""
    # TODO: compare objects or turn into lambda filter
    for model in models:
        if model.sim == livery.sim and model.pmdg_product == livery.pmdg_product:
            return model
    raise ValueError(f"No matching airplane in sim folder for {livery.sim} {livery.pmdg_product}")


def move_panel_file(src_folder: Path, game_path: Path, livery: AircraftLivery, dry_run: bool) -> None:
    """Move Aircraft.ini to PMDG product folder."""
    panel_file = src_folder / "Aircraft.ini"
    if not panel_file.is_file() or not livery.atc_id:
        return

    dest = game_path / f"PMDG/{livery.pmdg_product}/Aircraft/{livery.atc_id}.ini"
    print(f"Moving '{panel_file}' to '{dest}'\n")
    if not dry_run:
        shutil.move(panel_file, dest)


def append_livery_config_to_aircraft_cfg(src_cfg_txt: str, output_ac_cfg: Path, dry_run: bool) -> None:
    next_index = get_next_fltsim_section_index(output_ac_cfg)
    new_content = re.sub(r"^\[fltsim\.x\]", f"[fltsim.{next_index}]", src_cfg_txt, flags=re.MULTILINE)

    first_line = new_content.split("\n")[0]
    print(f"Adding {first_line} to '{output_ac_cfg}'\n")

    if not dry_run:
        with output_ac_cfg.open("a", encoding="utf-8") as f:
            f.write("\n\n" + new_content)


def install_livery_config_to_aircraft(
    src_folder: Path, game_path: Path, models: list[AircraftConfig], dry_run: bool
) -> Path:
    src_cfg_txt = (src_folder / "Config.cfg").read_text(encoding="utf-8")
    livery: AircraftLivery = next(AircraftLivery.from_aircraft_config_text(src_cfg_txt, src_folder))  # type: ignore
    model = find_matching_model(livery, models)

    sim_airplane_dir = game_path / AIRPLANES_DIR / model.relative_path
    move_panel_file(src_folder, game_path, livery, dry_run)
    append_livery_config_to_aircraft_cfg(src_cfg_txt, sim_airplane_dir / "aircraft.cfg", dry_run)

    return sim_airplane_dir


def extract_and_install_ptp_livery(ptp_path, game_path: Path, livery_path: Path, models, dry_run):
    print(f"Processing {livery_path.name}")
    stdout = run_ptp(ptp_path, livery_path).stdout.splitlines()
    if stdout[-1] == "DONE!":
        cab_line = "Extracting whole cab to:"
        src_folder = Path(
            [x.split(cab_line)[1].strip() for x in stdout if cab_line in x][0]
        )  # TODO: split, exception handling ?
        if not src_folder.exists():
            src_folder = livery_path.with_suffix("")  # assume dir is named same as file
        if not src_folder.exists():
            raise FileNotFoundError("PTP extraction path not found")

        airplane_dir = install_livery_config_to_aircraft(src_folder, game_path, models, dry_run)
        move_texture_dirs(src_folder, airplane_dir, dry_run)


def run_ptp(ptp, file_path: Path):
    return subprocess.run(
        [ptp, file_path],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _find_ptp():
    p = Path("./ptp_converter.exe")  # always prefer local, in case of breaking OC3 upgrades
    if p.exists():
        return p.resolve()
    p = Path(os.getenv("APPDATA")) / "PMDG/PMDG Operations Center/ptp_converter.exe"  # type: ignore
    if p.exists():
        return p.resolve()
    return None


def parse_args(argv: list[str]) -> tuple[Path, list[Path], bool]:
    dry_run = False
    discovery_run = False
    i = 1
    games = get_games_paths()

    try:
        if argv[i] == "-n":
            dry_run = True
            i += 1
        elif argv[i] == "-f":
            discovery_run = True
            i += 1

        game_path = games.get(argv[i].lower())
        if not game_path:
            game_path = Path(argv[2])
        assert game_path.is_dir()

        if discovery_run:
            models = list(discover_aircraft_in_sim(game_path))
            print(f"Found inside '{game_path}' models:")
            print(*models, sep="\n")
            sys.exit(0)

        # game_path = Path(argv[i]) if Path(argv[i]).is_dir() else games[argv[i]]
        # file_paths = [Path(arg).resolve() for arg in argv[i + 1 :]]
        file_paths = []
        for arg in argv[i + 1 :]:
            livery_path = Path(arg).resolve()

            if livery_path.suffix.lower() != ".ptp":
                print(f"Warning: Wrong extension for file {livery_path}")
            if not livery_path.is_file():
                print(f"Warning: Skipping missing file: {livery_path}")
            else:
                file_paths.append(livery_path)

        return game_path, file_paths, dry_run

    except (IndexError, AssertionError):
        print("Usage: python main.py [-n][-f] <sim short name or sim path> <file1> <file2> ...\n\nFound sims:")
        print(*games, sep="\n")
        sys.exit(1)


if __name__ == "__main__":
    ptp = _find_ptp()
    if not ptp:
        print("Error: ptp_converter.exe not found.")
        print(f"Install PMDG Operations Center 3 or put ptp_converter.exe in '{Path('./').resolve()}' folder.")
        sys.exit(1)

    game_path, file_paths, dry_run = parse_args(sys.argv)

    if dry_run:
        print("Info: Dry Run\n")

    models = list(discover_aircraft_in_sim(game_path))

    for file_path in file_paths:
        try:
            extract_and_install_ptp_livery(ptp, game_path, file_path, models, dry_run)
        except subprocess.CalledProcessError as e:
            if "PMDG OC3 PTP CLI Converter Tool" in e.stdout:
                print("Error: Unknown error in PMDG OC3 PTP CLI Converter Tool.")
                print("Perhaps you need older ptp_converter.exe version.\nExiting")
            else:
                print(f"Error: Process failed (exit {e.returncode}): {e.stderr or e.stdout}")
        except ValueError as e:
            print(e)
# TODO: add unziping
