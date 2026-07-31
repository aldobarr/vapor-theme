# Vapor

This project packages the KDE Plasma desktop visuals from
[Bazzite](https://github.com/ublue-os/bazzite) as an independent,
user-local theme. It is for ordinary Plasma desktop sessions—not Steam Gaming
Mode—and appears in KDE System Settings with the exact name **Vapor**.

This is an automated, source-pinned repackaging project. It is not affiliated
with or endorsed by Bazzite, Universal Blue, Valve, or KDE.

## Included

- Plasma 6 Global Theme `com.valve.vapor.desktop`
- Vapor Plasma Style with Bazzite's adaptive-transparency settings
- Vapor application color scheme
- Bazzite-modified Vapor splash
- Convergence JXL wallpaper package
- Project-unique Bazzite launcher icon
- Plasma 6 creation-time defaults for Folder View, Kickoff, and system-tray
  icon scaling

The release excludes RPM packaging, Steam Gaming Mode and
gamescope integration, VGUI2, GTK themes, Konsole profiles, Steam helpers,
Deck-specific system presets, and lock-screen configuration.

## Requirements

- KDE Plasma 6
- Python 3.11 or newer
- `kpackagetool6`, `plasma-apply-lookandfeel`,
  `plasma-apply-desktoptheme`, `plasma-apply-colorscheme`, `kiconfinder6`,
  `qtpaths6`, `kreadconfig6`, and `kwriteconfig6`
- Qt 6 JXL image support; on Fedora this is supplied by
  `kf6-kimageformats`

The project is tested against Fedora 44's current Plasma 6 packages. On a
normal Fedora KDE installation, most requirements are already present:

```sh
sudo dnf install kf6-kiconthemes kf6-kimageformats kf6-kpackage plasma-workspace
```

The Vapor installer itself does not use `sudo` and never writes to `/usr` or
`/etc`.

## Install and select Vapor

Download the archive and `SHA256SUMS` from the same GitHub release. Verify the
download:

```sh
sha256sum --check SHA256SUMS
```

GitHub also publishes an artifact attestation for every release asset. It can
be verified with `gh attestation verify` using this repository as the
predicate repository.

Extract the archive and run its installer:

```sh
tar -xzf Vapor-v*.tar.gz
cd vapor-*/
chmod +x ./install.sh ./uninstall.sh
./install.sh
```

Installation places the components beneath the current user's
`$XDG_DATA_HOME` (normally `~/.local/share`). It does **not** activate Vapor or
change any KDE setting.

Open **System Settings → Colors & Themes → Global Theme**, select **Vapor**,
and apply its appearance. Normal appearance application changes the color
scheme, Plasma Style, and splash. It does not reset panels, widgets, desktop
containments, a custom desktop wallpaper, or lock-screen settings.

Convergence is installed as a normal wallpaper named **Convergence**. Choose
it manually for the desktop or lock screen if desired. Vapor never changes an
existing lock-screen selection.

Plasma treats "Desktop and window layout" as a separate destructive choice:
that operation deletes and recreates panels, desktops, and widgets. It is not
part of installation or normal appearance application.

## Uninstall

Run the uninstaller from the extracted release:

```sh
./uninstall.sh
```

If Vapor is active, the uninstaller first applies Breeze. If another theme is
active, it is left unchanged. Only files recorded in Vapor's ownership state
are removed.

An independently selected Vapor component is retained rather than leaving a
dangling KDE reference. For example, if Convergence is still the desktop or
lock-screen wallpaper, the uninstaller reports and keeps that wallpaper
without changing the selection. Change the selection and run
`./uninstall.sh` again to remove the retained asset.

## Licensing and provenance

Project code is licensed under AGPL-3.0-only. Generated releases retain the
applicable upstream license files, content hashes, source commits, and
provenance. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for the
upstream visual sources and notices.
