import argparse
import sys
import yaml
import subprocess

from yattag import Doc, indent
from pathlib import Path
from pypdf import PdfWriter
from zipfile import ZipFile

TABLE_OF_CONTENTS_CSS = """
    ol {
        counter-reset: item;
        font-size: 28pt;
    }

    ol ol {
        font-size: 24pt;
    }

    ol li {
        display: block;
    }

    ol li:before {
        content: counters(item, ".") ". ";
        counter-increment: item;
    }
"""


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--link",
        metavar="CONFIG",
        help="Create links to slides in a directory structure for delivery",
    )
    group.add_argument(
        "--html",
        metavar="CONFIG",
        help="Create HTML pages for slides in a directory structure for delivery",
    )
    group.add_argument(
        "--pdf",
        metavar="CONFIG",
        help="Create PDFs for slides in a directory structure for delivery",
    )
    args = parser.parse_args()

    if args.link:
        package_material = False
        config_path = Path(args.link)
        output_format = "md"
        action = create_links
    elif args.html:
        package_material = False
        config_path = Path(args.html)
        output_format = "html"
        action = create_html
    elif args.pdf:
        package_material = True
        config_path = Path(args.pdf)
        output_format = "pdf"
        action = create_pdf
    else:
        raise RuntimeError("Action missing, we should not get here")

    with open(config_path, "r") as config_file:
        config = yaml.safe_load(config_file)

    table_of_contents, output_dir, extra_paths = create_filetree(
        config, config_path.parent, output_format, action
    )
    course_slides = None
    course_archive = None
    if package_material:
        course_slides = merge_course_slides(config, table_of_contents, output_dir)
        course_archive = zip_course_material(
            config, output_dir, extra_paths, course_slides
        )
    generate_index_page(
        table_of_contents,
        course_slides,
        course_archive,
        output_dir,
        config,
        package_material,
    )


def create_filetree(config, config_dir, output_format, action):
    root_path = Path() if "root" not in config else Path(config["root"])
    root_dir = root_path if root_path.is_absolute() else Path(config_dir, root_path)
    output_path = "output" if "output" not in config else Path(config["output"])
    output_dir = (
        output_path if output_path.is_absolute() else Path(config_dir, output_path)
    )

    table_of_contents = {}
    extra_paths_per_chapter = {}
    for chapter_title, chapter in config["chapters"].items():
        chapter_root = Path(root_dir, "" if "root" not in chapter else chapter["root"])
        chapter_output = Path(output_dir, chapter_title.replace(" ", "_"))
        chapter_output.mkdir(parents=True, exist_ok=True)

        if "assets" in chapter:
            assets = Path(chapter["assets"])
            chapter_assets_dest = Path(chapter_output, assets)
            chapter_assets_dest.unlink(missing_ok=True)
            assets_dir = assets if assets.is_absolute() else Path(chapter_root, assets)
            chapter_assets_dest.symlink_to(assets_dir, target_is_directory=True)
            assert chapter_assets_dest.exists(), f"Link is wrong: {chapter_assets_dest}"

        extras = [] if "extras" not in chapter else chapter["extras"]
        chapter_extras = []
        for extra_path in extras:
            extra_path = Path(extra_path)
            extra_src = (
                extra_path
                if extra_path.is_absolute()
                else Path(chapter_root, extra_path)
            )
            assert extra_src.exists(), f"Extra file {extra_src} does not exist"
            chapter_extras.append(extra_src)
        if chapter_extras:
            extra_paths_per_chapter[chapter_title] = {}
            extra_paths_per_chapter[chapter_title]["extras"] = chapter_extras
            extra_paths_per_chapter[chapter_title]["root"] = chapter_root

        chapter_slides = []
        slide_number = 0  # Number the slides to appear ordered when using marp --server
        for slide_title, slide_path in chapter["slides"].items():
            slide_src = Path(chapter_root, slide_path)
            assert slide_src.is_file(), f"Slide {slide_src} does not exist"
            dest_filename = Path(f"{slide_number:03}-{Path(slide_path).name}")
            slide_dest = Path(
                chapter_output, dest_filename.with_suffix(f".{output_format}")
            )
            slide_dest.parent.mkdir(parents=True, exist_ok=True)
            action(slide_src, slide_dest, config)
            chapter_slides.append((slide_title, slide_dest))
            slide_number += 1

        table_of_contents[chapter_title] = chapter_slides

    return table_of_contents, output_dir, extra_paths_per_chapter


def create_links(slide_src, slide_dest, _):
    slide_dest.unlink(missing_ok=True)
    slide_dest.symlink_to(slide_src)
    assert slide_dest.exists(), f"Link is wrong: {slide_dest}"


def create_html(slide_src, slide_dest, config):
    run_marp(slide_src, slide_dest, config, "--html")


def create_pdf(slide_src, slide_dest, config):
    run_marp(slide_src, slide_dest, config, "--pdf")


def run_marp(slide_src, slide_dest, config, output_type_flag):
    marp = Path("marp" if "marp" not in config else config["marp-cli"])
    subprocess.check_call(
        [marp, slide_src, output_type_flag, "--allow-local-files", "-o", slide_dest]
    )


def merge_course_slides(config, table_of_contents, output_dir):
    with PdfWriter() as merger:
        for chapter_slides in table_of_contents.values():
            for _, slide_path in chapter_slides:
                merger.append(slide_path)
        course_slides = Path(
            f'{config["title"].replace(" ", "_")}.pdf'
            if "course_slides" not in config
            else config["course_slides"]
        )
        course_slides = (
            course_slides
            if course_slides.is_absolute()
            else Path(output_dir, course_slides)
        )
        merger.write(course_slides)

    return course_slides


def zip_course_material(config, output_dir, extra_paths, course_slides):
    course_slides = Path(output_dir, course_slides)
    course_archive = Path(
        f'{config["title"].replace(" ", "_")}.zip'
        if "course_archive" not in config
        else config["course_archive"]
    )
    course_archive = (
        course_archive
        if course_archive.is_absolute()
        else Path(output_dir, course_archive)
    )
    with ZipFile(course_archive, "w") as zip_file:
        # Add slides
        zip_file.write(course_slides, course_slides.name)
        # Add extra files
        for chapter_title, chapter_extras in extra_paths.items():
            chapter_title = chapter_title.replace(" ", "_")
            chapter_root = chapter_extras["root"]
            for extra_path in chapter_extras["extras"]:
                # If the path is a directory, add all files in it preserving the directory structure
                relative_to_chapter_root = extra_path.is_relative_to(chapter_root)
                if extra_path.is_dir():
                    for extra_file in extra_path.rglob("*"):
                        zip_file.write(
                            extra_file,
                            Path(
                                chapter_title,
                                extra_file.relative_to(chapter_root)
                                if relative_to_chapter_root
                                else extra_file.relative_to(extra_path.parent),
                            ),
                        )
                else:
                    zip_file.write(
                        extra_path,
                        Path(
                            chapter_title,
                            extra_path.relative_to(chapter_root)
                            if relative_to_chapter_root
                            else extra_path.name,
                        ),
                    )

    return course_archive


def generate_index_page(
    table_of_contents,
    course_slides,
    course_archive,
    output_dir,
    config,
    package_material,
):
    with open(Path(output_dir, "index.html"), "w") as index_file:
        doc, tag, text = Doc().tagtext()
        with tag("html"):
            with tag("head"):
                with tag("title"):
                    text(config["title"])
                with tag("style"):
                    text(TABLE_OF_CONTENTS_CSS)
            with tag("body"):
                # Title
                with tag("h1"):
                    text(config["title"])
                # Table of contents
                with tag("ol", type="1"):
                    for chapter_title, chapter_slides in table_of_contents.items():
                        with tag("li"):
                            text(chapter_title)
                            with tag("ol", type="1"):
                                for slide_title, slide_path in chapter_slides:
                                    with tag("li"):
                                        with tag("a", href=f"{slide_path}"):
                                            text(slide_title)
                if package_material:
                    doc.stag("hr")
                    with tag("a", href=f"{course_slides}", style="font-size: 24pt"):
                        text("Course slides")
                    doc.stag("br")
                    with tag("a", href=f"{course_archive}", style="font-size: 24pt"):
                        text("Course archive")
        index_file.write(indent(doc.getvalue(), indent_text=True))


if __name__ == "__main__":
    sys.exit(main())