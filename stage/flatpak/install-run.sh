rm -rf build* .flatpak-builder/
flatpak-builder --user --install --force-clean build-dir org.nbar.Nbar.yaml
flatpak run org.nbar.Nbar
