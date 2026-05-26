from __future__ import annotations

import tempfile
import tomllib
import unittest
from pathlib import Path

from bb9 import install


class InstallTests(unittest.TestCase):
    def test_pyproject_install_metadata_points_to_existing_files(self) -> None:
        pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
        pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))

        readme = pyproject["project"]["readme"]
        scripts = pyproject["project"]["scripts"]
        package_data = pyproject["tool"]["setuptools"]["package-data"]["bb9"]

        self.assertTrue((pyproject_path.parent / readme).is_file())
        self.assertEqual("bb9.__main__:main", scripts["bb9"])
        self.assertIn("README.md", package_data)
        self.assertIn("tools/**/*", package_data)
        self.assertIn("templates/**/*", package_data)

    def test_installer_points_python_path_to_repo_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pth = install.install_user_site(Path(tmp))

            self.assertEqual(install.REPO_ROOT, Path(__file__).resolve().parents[1])
            self.assertEqual(str(install.REPO_ROOT), pth.read_text(encoding="utf-8").strip())

    def test_default_skill_templates_are_installed_without_overwriting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old_skills_dir = install.USER_SKILLS_DIR
            install.USER_SKILLS_DIR = Path(tmp) / "skills"
            try:
                install.install_default_skills()

                plan = install.USER_SKILLS_DIR / "plan" / "SKILL.md"
                plan_cli = install.USER_SKILLS_DIR / "plan" / "cli.py"
                dev = install.USER_SKILLS_DIR / "dev" / "SKILL.md"
                dev_cli = install.USER_SKILLS_DIR / "dev" / "cli.py"
                self.assertTrue(plan.is_file())
                self.assertTrue(plan_cli.is_file())
                self.assertTrue(dev.is_file())
                self.assertTrue(dev_cli.is_file())

                plan.write_text("# Custom plan\n", encoding="utf-8")
                install.install_default_skills()

                self.assertEqual("# Custom plan\n", plan.read_text(encoding="utf-8"))
                self.assertTrue(plan_cli.is_file())
                self.assertTrue(dev.is_file())
                self.assertTrue(dev_cli.is_file())
            finally:
                install.USER_SKILLS_DIR = old_skills_dir

    def test_launcher_uses_installing_python_executable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            launcher = install.install_launcher(Path(tmp), python_executable="/opt/homebrew/bin/python3.11")

            self.assertEqual(
                "#!/usr/bin/env sh\nexec /opt/homebrew/bin/python3.11 -m bb9 \"$@\"\n",
                launcher.read_text(encoding="utf-8"),
            )
            self.assertTrue(launcher.stat().st_mode & 0o111)

    def test_windows_launcher_creates_cmd_and_powershell_scripts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            launcher = install.install_launcher(
                Path(tmp),
                python_executable=r"C:\Python311\python.exe",
                os_name="nt",
            )

            self.assertEqual("bb9.cmd", launcher.name)
            self.assertEqual(
                '@echo off\n"C:\\Python311\\python.exe" -m bb9 %*\n',
                launcher.read_text(encoding="utf-8"),
            )
            self.assertEqual(
                "& 'C:\\Python311\\python.exe' -m bb9 @args\n",
                (Path(tmp) / "bb9.ps1").read_text(encoding="utf-8"),
            )

    def test_posix_path_update_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            bin_dir = home / ".local" / "bin"

            self.assertTrue(install.ensure_posix_user_path(bin_dir, home=home))
            self.assertFalse(install.ensure_posix_user_path(bin_dir, home=home))

            for name in (".zshrc", ".bashrc", ".profile"):
                text = (home / name).read_text(encoding="utf-8")
                self.assertEqual(1, text.count("# BB9 local commands"))
                self.assertIn(str(bin_dir), text)

    def test_posix_path_update_replaces_old_bb9_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            bin_dir = home / ".local" / "bin"
            zshrc = home / ".zshrc"
            zshrc.write_text(
                "export EDITOR=vim\n\n"
                "# BB9 local commands\n"
                'export PATH="$HOME/.local/bin:$PATH"\n',
                encoding="utf-8",
            )

            self.assertTrue(install.ensure_posix_user_path(bin_dir, home=home))
            text = zshrc.read_text(encoding="utf-8")

        self.assertIn("export EDITOR=vim", text)
        self.assertEqual(1, text.count("# BB9 local commands"))
        self.assertIn(f'export PATH="{bin_dir}:$PATH"', text)
        self.assertNotIn("$HOME/.local/bin", text)

    def test_path_value_append_respects_platform_separator(self) -> None:
        self.assertEqual(
            r"C:\Tools;C:\Users\x\AppData\Roaming\Python\Scripts",
            install.append_path_value(
                r"C:\Tools",
                Path(r"C:\Users\x\AppData\Roaming\Python\Scripts"),
                os_name="nt",
            ),
        )
        self.assertEqual(
            "/usr/bin:/home/x/.local/bin",
            install.append_path_value("/usr/bin", Path("/home/x/.local/bin"), os_name="posix"),
        )


if __name__ == "__main__":
    unittest.main()
