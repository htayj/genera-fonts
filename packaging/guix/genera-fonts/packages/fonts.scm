(define-module (genera-fonts packages fonts)
  #:use-module (gnu packages python)
  #:use-module (gnu packages python-xyz)
  #:use-module (gnu packages xorg)
  #:use-module (guix build-system gnu)
  #:use-module (guix gexp)
  #:use-module (guix packages)
  #:use-module ((guix licenses) #:prefix license:)
  #:use-module (srfi srfi-13))

;; Local/manual packages.  The source snapshot contains the inert extracted
;; records needed to regenerate the typefaces, but the package output is
;; produced only through stage-linux-package.sh and contains no source JSON.
(define %repository-root
  (canonicalize-path (string-append (dirname (current-filename)) "/../../../..")))

(define (selected-source? file stat)
  (let* ((prefix (string-append %repository-root "/"))
         (relative (if (string=? file %repository-root)
                       ""
                       (and (string-prefix? prefix file)
                            (string-drop file (string-length prefix)))))
         (components (and relative (string-split relative #\/))))
    (and relative
         (not (member ".git" components))
         (not (member "dist" components))
         (not (member "release" components))
         (not (member "packages" components))
         (not (member "__pycache__" components))
         (not (string-suffix? ".pyc" relative)))))

(define %genera-fonts-source
  (local-file %repository-root "genera-fonts-local-source"
              #:recursive? #t #:select? selected-source?))

(define %local-version "0+git.local")
(define %source-date-epoch "1")

(define (genera-fonts-package group synopsis description)
  (package
    (name (string-append "genera-fonts-" group))
    (version %local-version)
    (source %genera-fonts-source)
    (build-system gnu-build-system)
    (arguments
     (list
      #:phases
      #~(modify-phases %standard-phases
          (delete 'bootstrap)
          (delete 'configure)
          (replace 'build
            (lambda _
              (setenv "LC_ALL" "C")
              (setenv "PYTHONDONTWRITEBYTECODE" "1")
              (setenv "SOURCE_DATE_EPOCH" #$%source-date-epoch)
              (invoke "python3" "scripts/build.py" "--output" "dist" "--clean")
              (invoke "python3" "scripts/build_release.py"
                      "--distribution" "dist"
                      "--release-dir" "dist/release"
                      "--version" #$%local-version
                      "--source-date-epoch" #$%source-date-epoch)))
          (replace 'check
            (lambda* (#:key tests? #:allow-other-keys)
              (when tests?
                (invoke "python3" "scripts/check_release.py"
                        "--release-dir" "dist/release")
                (invoke "python3" "scripts/check_otb.py"
                        (string-append "dist/release/Genera-fonts-latin-"
                                       #$%local-version ".tar.gz")
                        (string-append "dist/release/Genera-fonts-symbols-"
                                       #$%local-version ".tar.gz")))))
          (replace 'install
            (lambda* (#:key outputs #:allow-other-keys)
              (let ((out (assoc-ref outputs "out")))
                (invoke "bash" "scripts/stage-linux-package.sh"
                        "--format" "guix"
                        "--group" #$group
                        "--destdir" out
                        "--version" #$%local-version
                        "--release-dir" "dist/release")
                (when (or (file-exists? (string-append out "/share/fonts/genera-fonts/"
                                                        #$group "/raw"))
                          (pair? (find-files out "\\.vlod$")))
                  (error "source or raw font material entered the Guix output"))))))))
    (native-inputs (list fonttosfnt python python-fonttools))
    (synopsis synopsis)
    (description description)
    (home-page "https://github.com/htayj/genera-fonts")
    ;; non-copyleft is used only to make Guix point at the required custom
    ;; notice.  NOTICE.md documents a statutory redistribution basis rather
    ;; than an upstream license grant.  Repository-authored work is BSD-3.
    (license (list license:bsd-3
                   (license:non-copyleft
                    "file://NOTICE.md"
                    "Typeface redistribution basis; not a license grant.")))))

(define-public genera-fonts-latin
  (genera-fonts-package
   "latin"
   "Genera bitmap fonts containing visible Basic Latin glyphs"
   "This package contains complete Genera 8.5 resident fonts selected by
visible Basic Latin glyph content.  It installs ISO 10646-1 BDF typefaces and
display-equivalent OTB conversions, with required provenance and notices."))

(define-public genera-fonts-symbols
  (genera-fonts-package
   "symbols"
   "Genera specialty bitmap fonts without visible Basic Latin glyphs"
   "This package contains the complementary Genera 8.5 resident specialty
fonts without visible Basic Latin letters.  It installs ISO 10646-1 BDF
typefaces and display-equivalent OTB conversions, with required provenance and
notices."))
