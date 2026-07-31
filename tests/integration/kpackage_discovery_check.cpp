#include <KPackage/Package>
#include <KPackage/PackageLoader>

#include <QCoreApplication>
#include <QFileInfo>
#include <KPluginMetaData>

#include <iostream>

int main(int argc, char *argv[])
{
    QCoreApplication application(argc, argv);
    if (application.arguments().size() != 2) {
        std::cerr << "usage: kpackage-discovery-check WALLPAPER_PACKAGE\n";
        return 2;
    }
    const QString globalThemeId =
        QStringLiteral("com.valve.vapor.desktop");
    const QString wallpaperImage = QStringLiteral("3940x2160.jxl");

    bool foundGlobalTheme = false;
    const auto metadata =
        KPackage::PackageLoader::self()->listPackages(
            QStringLiteral("Plasma/LookAndFeel"));
    for (const KPluginMetaData &candidate : metadata) {
        if (candidate.pluginId() != globalThemeId) {
            continue;
        }
        const KPackage::Package package =
            KPackage::PackageLoader::self()->loadPackage(
                QStringLiteral("Plasma/LookAndFeel"), candidate.pluginId());
        const bool passesSystemSettingsFilter =
            !package.filePath(QByteArrayLiteral("defaults")).isEmpty()
            || !package.filePath(QByteArrayLiteral("layouts")).isEmpty();
        if (package.metadata().isValid() && passesSystemSettingsFilter) {
            foundGlobalTheme = true;
        }
    }
    if (!foundGlobalTheme) {
        std::cerr << "System Settings loader did not discover Vapor\n";
        return 1;
    }

    KPackage::Package wallpaper =
        KPackage::PackageLoader::self()->loadPackage(
            QStringLiteral("Wallpaper/Images"));
    wallpaper.setPath(application.arguments().at(1));
    if (!wallpaper.isValid()
        || !wallpaper.entryList(QByteArrayLiteral("images"))
                .contains(wallpaperImage)
        || wallpaper.filePath(QByteArrayLiteral("images"), wallpaperImage)
               .isEmpty()) {
        std::cerr << "KDE image wallpaper loader did not discover Convergence\n";
        return 1;
    }

    std::cout << globalThemeId.toStdString() << '\n'
              << wallpaperImage.toStdString() << '\n';
    return 0;
}
