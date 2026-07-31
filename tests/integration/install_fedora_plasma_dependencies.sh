#!/usr/bin/env bash
set -euo pipefail

dnf install -y \
    dbus-daemon \
    cmake \
    extra-cmake-modules \
    gcc-c++ \
    kactivitymanagerd \
    kf6-kiconthemes \
    kf6-kimageformats \
    kf6-kcoreaddons-devel \
    kf6-kpackage \
    kf6-kpackage-devel \
    kwin-wayland \
    libcap \
    plasma-desktop \
    plasma-workspace \
    python3 \
    python3-pyqt6 \
    qt6-qtbase-devel \
    qt6-qttools \
    tar \
    xorg-x11-server-Xvfb
