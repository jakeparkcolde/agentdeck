"""영어 화면·터미널에 한국어가 새어 나오지 않는지 확인한다.

1) 화면 문구: 코드 안의 한국어 문자열(주석·docstring·정규식·로그 제외)을 영어 모드 tr() 에 넣어 한글이 남으면 실패.
   새 문구를 넣고 i18n.py 사전에 영어를 안 넣으면 여기서 걸린다.
2) 터미널 문구: print / SystemExit / RuntimeError 안의 한국어는 반드시 t("한국어", "English") 로 감싸야 한다.

번역하면 안 되는 데이터(파싱용 단어, 한국 시장 업종 이름 등)는 ALLOW 에 넣거나 그 줄 끝에 `# i18n: skip` 을 단다.
"""

import ast
import re
import subprocess
import unittest
from pathlib import Path

import i18n

ROOT = Path(__file__).resolve().parent.parent
HANGUL = re.compile("[가-힣]")
# 화면에 나가지 않거나 실제 데이터라 번역하지 않는 파일
SKIP_FILES = {"i18n.py", "demo.py", "preview.py", "market.py"}
SKIP_PREFIX = ("tests/",)
ALLOW = {"오후"}  # web_credits: Flow 의 한국어 날짜를 읽는 단어
LOG_CALLS = {"print", "log", "SystemExit", "RuntimeError"}
PARSE_CALLS = {"compile", "search", "match", "sub", "findall", "fullmatch", "split", "finditer"}


def py_files():
    out = subprocess.run(["git", "ls-files", "*.py"], cwd=ROOT, capture_output=True, text=True).stdout.split()
    return [f for f in out if f not in SKIP_FILES and not f.startswith(SKIP_PREFIX) and (ROOT / f).exists()]


def call_name(node):
    fn = node.func
    return fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")


class Scan:
    def __init__(self, path):
        self.src = (ROOT / path).read_text()
        self.lines = self.src.splitlines()
        self.tree = ast.parse(self.src)
        self.parent = {c: p for p in ast.walk(self.tree) for c in ast.iter_child_nodes(p)}
        self.docs = {id(n.body[0].value) for n in ast.walk(self.tree)
                     if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                     and n.body and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}

    def calls_above(self, node):
        p = node
        while p in self.parent:
            p = self.parent[p]
            if isinstance(p, ast.Call):
                yield p

    def skipped_line(self, node):
        return "# i18n: skip" in self.lines[node.lineno - 1]

    def strings(self):
        """(노드, 영어 모드로 바꿔 볼 예시 문자열)"""
        for n in ast.walk(self.tree):
            if isinstance(n, ast.Constant) and isinstance(n.value, str):
                if id(n) in self.docs or isinstance(self.parent.get(n), (ast.JoinedStr, ast.FormattedValue)):
                    continue
                yield n, n.value
            elif isinstance(n, ast.JoinedStr):
                parts = []
                for v in n.values:
                    if isinstance(v, ast.Constant):
                        parts.append(v.value)
                    else:  # 자리 표시: 말 이름이면 말 이름, 나머지는 숫자로 바꿔 본다
                        parts.append("흑진주" if "race_names" in ast.unparse(v) else "3")
                yield n, "".join(parts)


def is_data(text):
    return "\n" in text or "|" in text or text in ALLOW


class ScreenTextTest(unittest.TestCase):
    def test_no_korean_left_in_english_mode(self):
        old, i18n.LANG = i18n.LANG, "en"
        try:
            leaks = []
            for f in py_files():
                s = Scan(f)
                for node, text in s.strings():
                    if not HANGUL.search(text) or is_data(text) or s.skipped_line(node):
                        continue
                    names = {call_name(c) for c in s.calls_above(node)}
                    if names & (LOG_CALLS | PARSE_CALLS | {"t"}):
                        continue
                    if HANGUL.search(i18n.tr(text) or ""):
                        leaks.append(f"{f}:{node.lineno}: {text!r} -> {i18n.tr(text)!r}")
            self.assertEqual(leaks, [], "i18n.py 에 영어가 빠진 화면 문구:\n" + "\n".join(leaks))
        finally:
            i18n.LANG = old


class TerminalTextTest(unittest.TestCase):
    def test_korean_terminal_messages_have_english(self):
        bad = []
        for f in py_files() + [p for p in ("scripts/install-hooks.py",) if (ROOT / p).exists()]:
            s = Scan(f)
            for node, text in s.strings():
                if not HANGUL.search(text) or s.skipped_line(node):
                    continue
                names = [call_name(c) for c in s.calls_above(node)]
                if "t" in names:
                    continue
                if set(names) & LOG_CALLS:
                    bad.append(f"{f}:{node.lineno}: {text!r}")
        self.assertEqual(bad, [], 't("한국어", "English") 로 감싸야 하는 터미널 문구:\n' + "\n".join(bad))


class LanguageTest(unittest.TestCase):
    def test_t_and_resolve(self):
        old = i18n.LANG
        try:
            i18n.LANG = "en"
            self.assertEqual(i18n.t("안녕", "hi"), "hi")
            i18n.LANG = "ko"
            self.assertEqual(i18n.t("안녕", "hi"), "안녕")
        finally:
            i18n.LANG = old
        self.assertEqual(i18n.resolve("ko"), "ko")
        self.assertEqual(i18n.resolve("en"), "en")
        self.assertIn(i18n.resolve("auto"), ("ko", "en"))
        self.assertIn(i18n.resolve(None), ("ko", "en"))


if __name__ == "__main__":
    unittest.main()
