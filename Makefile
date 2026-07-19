PYTHON ?= python3
VERSION ?= $(shell git describe --tags --always --dirty 2>/dev/null || printf 'v0.0.0-dev')
SOURCE_DATE_EPOCH ?= $(shell git show -s --format=%ct HEAD 2>/dev/null || printf '0')
RELEASE_DIR ?= dist/release
DIST_PACKAGE_DIR ?= dist/packages
CONTAINER_RUNTIME ?= docker

.PHONY: import check-import dist test check check-external reproducible \
	specimens check-specimens release check-release release-reproducible ci \
	package-deb package-rpm package-arch package-void packages \
	test-package-deb test-package-rpm test-package-arch test-package-void \
	test-packages test-nix-flake-container clean

import:
	$(PYTHON) scripts/import_genera_fonts.py --clean

check-import:
	$(PYTHON) scripts/import_genera_fonts.py --check

dist:
	$(PYTHON) scripts/build.py --clean

test:
	$(PYTHON) -m unittest discover -s tests -v

check: dist test
	$(PYTHON) scripts/check_dist.py
	$(PYTHON) scripts/check_rendering.py --output dist

check-external: check
	$(PYTHON) scripts/check_dist.py --external-tools
	$(PYTHON) scripts/check_rendering.py --output dist --external

reproducible:
	$(PYTHON) scripts/check_reproducibility.py

specimens: dist
	$(PYTHON) scripts/update_specimen_gallery.py

check-specimens: dist
	$(PYTHON) scripts/update_specimen_gallery.py --check

release: dist
	$(PYTHON) scripts/build_release.py \
		--distribution dist \
		--release-dir "$(RELEASE_DIR)" \
		--version "$(VERSION)" \
		--source-date-epoch "$(SOURCE_DATE_EPOCH)"

check-release: release
	$(PYTHON) scripts/check_release.py --release-dir "$(RELEASE_DIR)"
	$(PYTHON) scripts/check_otb.py \
		"$(RELEASE_DIR)/Genera-fonts-latin-$(VERSION).tar.gz" \
		"$(RELEASE_DIR)/Genera-fonts-symbols-$(VERSION).tar.gz"

release-reproducible: dist
	$(PYTHON) scripts/check_release_reproducibility.py \
		--distribution dist \
		--version "$(VERSION)" \
		--source-date-epoch "$(SOURCE_DATE_EPOCH)"

ci: check-external reproducible check-specimens check-release release-reproducible

package-deb: release
	SOURCE_DATE_EPOCH="$(SOURCE_DATE_EPOCH)" DIST_PACKAGE_DIR="$(DIST_PACKAGE_DIR)" \
		scripts/package-deb.sh --version "$(VERSION)"

package-rpm: release
	SOURCE_DATE_EPOCH="$(SOURCE_DATE_EPOCH)" DIST_PACKAGE_DIR="$(DIST_PACKAGE_DIR)" \
		scripts/package-rpm.sh --version "$(VERSION)"

package-arch: release
	SOURCE_DATE_EPOCH="$(SOURCE_DATE_EPOCH)" DIST_PACKAGE_DIR="$(DIST_PACKAGE_DIR)" \
		scripts/package-arch.sh --version "$(VERSION)"

package-void: release
	SOURCE_DATE_EPOCH="$(SOURCE_DATE_EPOCH)" DIST_PACKAGE_DIR="$(DIST_PACKAGE_DIR)" \
		CONTAINER_RUNTIME="$(CONTAINER_RUNTIME)" \
		scripts/package-void.sh --version "$(VERSION)" --runtime "$(CONTAINER_RUNTIME)"

packages: package-deb package-rpm package-arch package-void

test-package-deb:
	@set --; for package in "$(DIST_PACKAGE_DIR)"/deb/*.deb; do \
		test -f "$$package" || continue; set -- "$$@" --package "$$package"; \
	done; test "$$#" -eq 4; \
	scripts/test-linux-package-container.sh --runtime "$(CONTAINER_RUNTIME)" \
		--format deb "$$@"

test-package-rpm:
	@set --; for package in "$(DIST_PACKAGE_DIR)"/rpm/*.rpm; do \
		test -f "$$package" || continue; set -- "$$@" --package "$$package"; \
	done; test "$$#" -eq 4; \
	scripts/test-linux-package-container.sh --runtime "$(CONTAINER_RUNTIME)" \
		--format rpm "$$@"

test-package-arch:
	@set --; for package in "$(DIST_PACKAGE_DIR)"/arch/*.pkg.tar.*; do \
		test -f "$$package" || continue; \
		case "$$package" in *.sha256|*.sig) continue;; esac; \
		set -- "$$@" --package "$$package"; \
	done; test "$$#" -eq 4; \
	scripts/test-linux-package-container.sh --runtime "$(CONTAINER_RUNTIME)" \
		--format arch "$$@"

test-package-void:
	@set --; for package in "$(DIST_PACKAGE_DIR)"/void/*.xbps; do \
		test -f "$$package" || continue; set -- "$$@" --package "$$package"; \
	done; test "$$#" -eq 4; \
	scripts/test-linux-package-container.sh --runtime "$(CONTAINER_RUNTIME)" \
		--format void "$$@"

test-packages: test-package-deb test-package-rpm test-package-arch test-package-void

test-nix-flake-container:
	scripts/test-nix-flake-container.sh --runtime "$(CONTAINER_RUNTIME)"

clean:
	rm -rf -- dist
