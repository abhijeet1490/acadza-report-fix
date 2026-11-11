"""Inspect data or emit a local preview of one question per type."""

import argparse
from collections import Counter
import html
from pathlib import Path

from .dataset import DEFAULT_DATA, load_dataset


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--preview", type=Path, help="Write a static HTML preview")
    args = parser.parse_args()
    dataset = load_dataset(args.data)
    print(f"Questions {len(dataset.questions)}, reports {len(dataset.reports)}, cases {len(dataset.cases)}")
    print(dict(Counter(q.question_type for q in dataset.questions)))
    if args.preview:
        sections = []
        for question_type in ("scq", "mcq", "integerQuestion"):
            question = next(
                q for q in dataset.questions if q.question_type == question_type
            )
            payload = question.payload
            sections.append(f"<h2>{question_type} · {question.question_id}</h2>")
            for field in ("question", "solution"):
                # Sandbox disallows scripts and isolates the preserved production HTML.
                document = (
                    '<meta charset="utf-8"><meta http-equiv="Content-Security-Policy" '
                    'content="default-src &#39;none&#39;; style-src &#39;unsafe-inline&#39;">'
                    + payload[field]
                )
                sections.append(
                    f"<h3>{field}</h3><iframe sandbox srcdoc=\"{html.escape(document, quote=True)}\"></iframe>"
                )
            sections.append(f"<p>Stored answer <code>{html.escape(str(payload['answer']))}</code></p>")
        args.preview.write_text(
            '<!doctype html><html lang="en"><meta charset="utf-8">'
            '<title>Acadza source preview</title><style>'
            'body{max-width:960px;margin:32px auto;font-family:system-ui}'
            'iframe{width:100%;height:350px;border:1px solid #ccc}'
            '</style><h1>Source question samples</h1>' + "".join(sections) + "</html>",
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
