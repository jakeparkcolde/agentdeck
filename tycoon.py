"""AI 스타트업 타이쿤: 주사위를 굴려 AI 회사를 인수하고 'API 사용료'를 받는 보드게임 (나 + 컴퓨터 2명).

화면
- 평소: 판의 말 주변 8칸을 위아래 두 줄의 왼쪽 네 칸씩(0~3, 5~8)에 그리고,
  오른쪽 끝 두 칸(4, 9)은 버튼 (굴리기 / 인수 / 증설 / 넘어가기). 넓은 칸은 세 사람의 돈과 차례.
- 연출: 주사위·찬스 카드·인수·사용료·파산·승패는 아래 줄과 넓은 칸까지 화면 전체를 쓴다.

규칙 (짧게 끝나게)
- 판은 32칸 (AI 회사 24곳 · 8개 업종). 화면은 움직이는 말 주변 8칸을 따라가고, 넓은 칸에 판 전체 작은 지도.
- 출발을 지나면 투자금 +150. 빈 회사에 서면 살 수 있고, 내 회사에 다시 서면 서버 증설(2단계) → 랜드마크 '유니콘 본사'(3단계).
- 특수 칸: 깜짝 뉴스(카드) · 서버 장애(한 번 쉬기) · AI 학회(상금) · 클라우드 정산(돈 내기) · 해커톤(한 번 더).
- 남의 회사에 서면 API 사용료를 낸다. 같은 업종 3곳을 다 가지면 사용료 2배.
- 업종을 넘는 조합을 갖추면 시너지 (SYNERGIES): 사용료 +50%, 정산 면제, 상금 2배, 차례마다 지원금.
- 깜짝 뉴스 중 보관 카드(행운 카드)는 갖고 있다가 쓴다: 사용료 면제 · 반값 인수 · 피벗(보이는 칸으로 이동) · 인수합병.
- 돈이 모자라면 회사를 반값에 팔고, 그래도 모자라면 파산. 혼자 남거나 각자 24번 굴리면 자산이 많은 사람이 이긴다.
"""

from __future__ import annotations

import io
import json
import math
import random
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw, ImageFont

from clock import _Draw, _kr
from i18n import t
from sfx import play

SIZE = 196
GAP = 40  # 칸 사이 테두리 (캔버스 좌표)
WIDE = (458, 196)
WIDE_X = 3 * (SIZE + GAP)
FW, FH = WIDE_X + WIDE[0], 3 * SIZE + 2 * GAP  # 화면 전체 캔버스
BW, BH = 4 * SIZE + 3 * GAP, 2 * SIZE + GAP  # 판 캔버스 (왼쪽 4열 × 2줄)
BOARD_KEYS = [0, 1, 2, 3, 5, 6, 7, 8]
BTN_A, BTN_B = 4, 9
FPS = 2  # 덱은 새 그림을 1초에 1~2장만 받아들인다 (더 보내면 화면이 밀림)
EMOJI_FONT = "/System/Library/Fonts/Apple Color Emoji.ttc"

START_CASH, PASS_BONUS, MAX_ROUNDS = 1500, 150, 24  # MAX_ROUNDS: 한 사람당 차례 수
BG = (14, 16, 24)
INK = (230, 233, 245)
DIM = (150, 156, 185)
PLAYER_COLORS = [(229, 72, 77), (59, 130, 246), (46, 196, 160)]  # 나 · 컴퓨터1 · 컴퓨터2
GROUP_COLORS = [(139, 92, 246), (236, 72, 153), (59, 130, 246), (20, 184, 166), (34, 197, 94), (234, 179, 8),
                (249, 115, 22), (239, 68, 68)]
ART = Path(__file__).parent / "assets" / "tycoon"  # agy(Antigravity) 로 만든 그림 256px WebP (없으면 이모지)
BONUS, TAX = 100, 100
MAX_LEVEL = 3  # 증설 1·2단계 + 3단계 랜드마크(유니콘 본사)
LEVEL_MULT = (1, 3, 6, 9)  # 단계별 사용료 배수
AGI_GRANT = 30  # AGI 레이스 시너지: 내 차례마다


@dataclass(frozen=True)
class Cell:
    kind: str  # start | company | chance | outage | bonus | tax | again
    name: str
    emoji: str  # 그림 파일이 없을 때 대신 쓰는 이모지
    art: str    # assets/tycoon/<art>.webp
    group: int = -1
    price: int = 0


def _company(art: str, name: str, emoji: str, group: int, price: int) -> Cell:
    return Cell("company", name, emoji, art, group, price)


def _special(kind: str, art: str, name: str, emoji: str) -> Cell:
    return Cell(kind, name, emoji, art)


# 32칸, 시계 방향. 네 칸마다 특수 칸, 그 사이에 같은 업종 회사 세 곳 (뒤로 갈수록 비쌈)
CELLS = [
    _special("start", "start", t("출발", "Start"), "🚀"),
    _company("chatbot", t("챗봇", "Chatbot"), "💬", 0, 100),
    _company("translator", t("번역 엔진", "Translator"), "🌐", 0, 110),
    _company("voice", t("음성 비서", "Voice AI"), "🎙️", 0, 120),
    _special("chance", "news", t("깜짝 뉴스", "Breaking"), "📰"),
    _company("imagegen", t("이미지 생성", "Image Gen"), "🎨", 1, 140),
    _company("videogen", t("영상 생성", "Video Gen"), "🎬", 1, 150),
    _company("musicgen", t("음악 생성", "Music Gen"), "🎵", 1, 160),
    _special("outage", "outage", t("서버 장애", "Outage"), "🔥"),
    _company("recommender", t("추천 엔진", "Recommender"), "🎯", 2, 180),
    _company("search", t("검색 AI", "AI Search"), "🔎", 2, 190),
    _company("agents", t("에이전트", "Agents"), "🤖", 2, 200),
    _special("bonus", "conference", t("AI 학회", "AI Summit"), "🎤"),
    _company("tutor", t("AI 튜터", "AI Tutor"), "🎓", 3, 220),
    _company("medical", t("의료 영상", "Med Imaging"), "🩺", 3, 230),
    _company("drug", t("신약 개발", "Drug Discovery"), "💊", 3, 240),
    _special("chance", "news", t("깜짝 뉴스", "Breaking"), "📰"),
    _company("selfdriving", t("자율주행", "Self-driving"), "🚗", 4, 260),
    _company("drone", t("드론 배송", "Drone Delivery"), "🚁", 4, 270),
    _company("robotaxi", t("로봇 택시", "Robotaxi"), "🚕", 4, 280),
    _special("tax", "tax", t("클라우드 정산", "Cloud Bill"), "🧾"),
    _company("robotarm", t("로봇 팔", "Robot Arm"), "🦾", 5, 300),
    _company("aichip", t("AI 칩", "AI Chip"), "🧠", 5, 310),
    _company("wearable", t("웨어러블", "Wearables"), "🕶️", 5, 320),
    _special("chance", "news", t("깜짝 뉴스", "Breaking"), "📰"),
    _company("gpucloud", t("GPU 클라우드", "GPU Cloud"), "☁️", 6, 340),
    _company("datacenter", t("데이터센터", "Data Center"), "🏢", 6, 350),
    _company("edge", t("엣지 서버", "Edge Servers"), "📡", 6, 360),
    _special("again", "hackathon", t("해커톤", "Hackathon"), "💻"),
    _company("quantum", t("양자 연구소", "Quantum Lab"), "⚛️", 7, 380),
    _company("space", t("우주 AI", "Space AI"), "🛰️", 7, 400),
    _company("agi", t("AGI 연구소", "AGI Lab"), "✨", 7, 420),
]
N = len(CELLS)


@dataclass(frozen=True)
class Card:
    title: str
    desc: str
    emoji: str
    art: str
    kind: str  # cash | each | per_company | goto_start | outage | forward | keep(보관)
    amount: int = 0
    item: str = ""  # keep 카드: 보관함에 들어가는 행운 카드 (ITEMS)


CARDS = [
    Card(t("투자 유치 성공", "Funding round!"), t("+200", "+200"), "💰", "card_funding", "cash", 200),
    Card(t("해커톤 우승", "Won a hackathon"), t("+100", "+100"), "🏆", "card_hackathon", "cash", 100),
    Card(t("클라우드 요금 폭탄", "Cloud bill shock"), t("-150", "-150"), "💸", "card_cloudbill", "cash", -150),
    Card(t("데이터 유출 과징금", "Data-leak fine"), t("-100", "-100"), "⚖️", "card_fine", "cash", -100),
    Card(t("바이럴 대박", "Gone viral"), t("모두에게서 50씩", "50 from everyone"), "📈", "card_viral", "each", 50),
    Card(t("인재 영입 전쟁", "Talent war"), t("회사마다 25씩 내기", "Pay 25 per company"), "🧑‍💻", "card_talent", "per_company", 25),
    Card(t("IPO 축하", "IPO day"), t(f"출발로 이동 +{PASS_BONUS}", f"Go to Start, +{PASS_BONUS}"), "🔔", "card_ipo", "goto_start"),
    Card(t("GPU 품귀", "GPU shortage"), t("서버 장애로 이동, 한 번 쉬기", "Go to Outage, skip a turn"), "🧯", "card_gpu", "outage"),
    Card(t("데모 데이 발표", "Demo day"), t("3칸 앞으로", "Move 3 forward"), "🎤", "card_demo", "forward", 3),
    Card(t("오픈소스 스타 폭발", "Open-source star"), t("+80", "+80"), "⭐", "card_oss", "cash", 80),
    Card(t("API 레이트 리밋", "Rate limited"), t("-80", "-80"), "⏳", "card_ratelimit", "cash", -80),
    Card(t("보안 감사 통과", "Passed security audit"), t("보관: 사용료 1번 면제", "Keep: skip one fee"), "🛡️", "card_shield", "keep", item="shield"),
    Card(t("시리즈 A 우대권", "Series A voucher"), t("보관: 다음 인수 반값", "Keep: next buy half price"), "🎟️", "card_coupon", "keep", item="coupon"),
    Card(t("피벗 기회", "Pivot chance"), t("보관: 보이는 칸으로 이동", "Keep: jump to a shown cell"), "🔀", "card_pivot", "keep", item="pivot"),
    Card(t("인수합병 제안서", "M&A offer"), t("보관: 남의 회사 1.5배에 인수", "Keep: buy a rival's company at 1.5x"), "🤝", "card_mna", "keep", item="mna"),
]

# 보관 카드(행운 카드): 키 → (이모지, 짧은 이름)
ITEMS = {"shield": ("🛡️", t("면제권", "Shield")), "coupon": ("🎟️", t("반값권", "Voucher")),
         "pivot": ("🔀", t("피벗", "Pivot")), "mna": ("🤝", t("인수합병", "M&A"))}


@dataclass(frozen=True)
class Synergy:
    key: str
    emoji: str
    name: str
    need: tuple  # 칸 그림 이름 묶음들. 묶음마다 하나 이상 가지면 됨
    effect: str
    groups: frozenset = frozenset()  # 사용료 +50% 가 붙는 업종 (없으면 사용료와 무관)


SYNERGIES = [
    Synergy("multimodal", "🎨", t("멀티모달", "Multimodal"), (("voice",), ("imagegen",), ("videogen",)),
            t("생성 AI 사용료 +50%", "Gen-AI fees +50%"), frozenset({0, 1})),
    Synergy("vertical", "☁️", t("수직 계열화", "Vertical stack"), (("aichip",), ("gpucloud",), ("datacenter",)),
            t("클라우드 정산 면제", "No cloud bill")),
    Synergy("mobility", "🚗", t("모빌리티 스택", "Mobility stack"), (("aichip",), ("selfdriving", "drone", "robotaxi"), ("edge",)),
            t("모빌리티 사용료 +50%", "Mobility fees +50%"), frozenset({4})),
    Synergy("health", "🩺", t("헬스케어 AI", "Healthcare AI"), (("medical",), ("drug",), ("tutor",)),
            t("학회 상금 2배", "Summit prize x2")),
    Synergy("agi", "✨", t("AGI 레이스", "AGI race"), (("agents",), ("aichip",), ("agi",)),
            t(f"차례마다 +{AGI_GRANT}", f"+{AGI_GRANT} each turn")),
]
SYN = {s.key: s for s in SYNERGIES}


def rent(cell: Cell, level: int, monopoly: bool, boost: float = 1.0) -> int:
    """API 사용료: 기본은 가격의 절반, 증설 1단계 ×3, 2단계 ×6, 랜드마크 ×9, 업종 독점이면 ×2, 시너지면 ×1.5."""
    base = cell.price // 2
    r = int(base * LEVEL_MULT[level] * boost)
    return r * 2 if monopoly else r


def upgrade_cost(cell: Cell, level: int = 0) -> int:
    """level 단계에서 한 단계 올리는 값: 증설은 가격의 절반, 랜드마크(2→3)는 가격만큼."""
    return cell.price if level >= 2 else cell.price // 2


def invested(cell: Cell, level: int) -> int:
    """증설에 들인 돈 합계 (자산·매각 계산용)."""
    return sum(upgrade_cost(cell, k) for k in range(level))


@dataclass
class Player:
    name: str
    color: tuple
    cpu: bool
    cash: int = START_CASH
    pos: int = 0
    skip: bool = False
    out: bool = False
    items: list = field(default_factory=list)  # 보관 카드 (ITEMS 키)


@dataclass
class Game:
    """규칙만 (화면·시간 없음). 테스트하기 쉽게 분리."""
    rnd: random.Random = field(default_factory=random.Random)
    players: list[Player] = field(default_factory=list)
    owner: dict[int, int] = field(default_factory=dict)   # 칸 → 플레이어
    level: dict[int, int] = field(default_factory=dict)   # 칸 → 증설 단계 0~2
    turn: int = 0
    rounds: int = 0
    deck: list[int] = field(default_factory=list)
    shielded: bool = False  # 마지막 사용료를 면제권으로 막았는지

    def __post_init__(self) -> None:
        if not self.players:
            self.players = [Player(t("나", "You"), PLAYER_COLORS[0], False),
                            Player(t("컴퓨터 1", "CPU 1"), PLAYER_COLORS[1], True),
                            Player(t("컴퓨터 2", "CPU 2"), PLAYER_COLORS[2], True)]

    @property
    def cur(self) -> Player:
        return self.players[self.turn]

    def monopoly(self, who: int, group: int) -> bool:
        cells = [i for i, c in enumerate(CELLS) if c.group == group]
        return all(self.owner.get(i) == who for i in cells)

    def synergies(self, who: int) -> list[str]:
        """who 가 갖춘 시너지 키."""
        arts = {CELLS[i].art for i, o in self.owner.items() if o == who}
        return [s.key for s in SYNERGIES if all(any(a in arts for a in alts) for alts in s.need)]

    def rent_at(self, i: int) -> int:
        o = self.owner.get(i)
        if o is None:
            return 0
        c = CELLS[i]
        boost = 1.5 if any(c.group in SYN[k].groups for k in self.synergies(o)) else 1.0
        return rent(c, self.level.get(i, 0), self.monopoly(o, c.group), boost)

    def assets(self, who: int) -> int:
        v = self.players[who].cash
        for i, o in self.owner.items():
            if o == who:
                v += CELLS[i].price + invested(CELLS[i], self.level.get(i, 0))
        return v

    def alive(self) -> list[int]:
        return [k for k, p in enumerate(self.players) if not p.out]

    def roll(self) -> tuple[int, int]:
        return self.rnd.randint(1, 6), self.rnd.randint(1, 6)

    def path(self, steps: int) -> list[int]:
        p = self.cur.pos
        return [(p + k) % N for k in range(1, steps + 1)]

    def move_to(self, pos: int, *, passing: bool = True) -> bool:
        """이동. 출발을 지나거나 도착하면 투자금 (True 반환)."""
        p = self.cur
        passed = passing and pos <= p.pos and pos != p.pos
        p.pos = pos
        if passed:
            p.cash += PASS_BONUS
        return passed

    def draw(self) -> Card:
        if not self.deck:
            self.deck = list(range(len(CARDS)))
            self.rnd.shuffle(self.deck)
        return CARDS[self.deck.pop()]

    def apply_card(self, c: Card) -> list[tuple[int, int]]:
        """돈 변화 [(플레이어, 금액)]. 이동 카드는 위치만 바꾼다 (도착 처리는 호출하는 쪽)."""
        me = self.turn
        out: list[tuple[int, int]] = []
        if c.kind == "keep":
            self.cur.items.append(c.item)
        elif c.kind == "cash":
            self.cur.cash += c.amount
            out.append((me, c.amount))
        elif c.kind == "each":
            for k in self.alive():
                if k != me:
                    self.players[k].cash -= c.amount
                    self.cur.cash += c.amount
                    out += [(k, -c.amount), (me, c.amount)]
        elif c.kind == "per_company":
            n = sum(1 for o in self.owner.values() if o == me)
            self.cur.cash -= c.amount * n
            out.append((me, -c.amount * n))
        elif c.kind == "goto_start":
            self.cur.pos = 0
            self.cur.cash += PASS_BONUS
            out.append((me, PASS_BONUS))
        elif c.kind == "outage":
            self.cur.pos = next(i for i, x in enumerate(CELLS) if x.kind == "outage")
            self.cur.skip = True
        elif c.kind == "forward":
            self.move_to((self.cur.pos + c.amount) % N)
        return out

    def buy_price(self, i: int) -> int:
        """반값권이 있으면 반값."""
        return CELLS[i].price // 2 if "coupon" in self.cur.items else CELLS[i].price

    def buy(self, i: int) -> bool:
        c = CELLS[i]
        price = self.buy_price(i)
        if c.kind != "company" or i in self.owner or self.cur.cash < price:
            return False
        if price < c.price:
            self.cur.items.remove("coupon")
        self.cur.cash -= price
        self.owner[i] = self.turn
        self.level[i] = 0
        return True

    def upgrade(self, i: int) -> bool:
        c = CELLS[i]
        lv = self.level.get(i, 0)
        if self.owner.get(i) != self.turn or lv >= MAX_LEVEL or self.cur.cash < upgrade_cost(c, lv):
            return False
        self.cur.cash -= upgrade_cost(c, lv)
        self.level[i] = lv + 1
        return True

    def takeover_cost(self, i: int) -> int:
        c = CELLS[i]
        return (c.price + invested(c, self.level.get(i, 0))) * 3 // 2

    def can_takeover(self, i: int) -> bool:
        """인수합병 제안서: 남의 회사(랜드마크 제외)를 1.5배 값에 사 온다."""
        o = self.owner.get(i)
        return ("mna" in self.cur.items and o is not None and o != self.turn
                and self.level.get(i, 0) < MAX_LEVEL and self.cur.cash >= self.takeover_cost(i))

    def takeover(self, i: int) -> bool:
        if not self.can_takeover(i):
            return False
        cost, o = self.takeover_cost(i), self.owner[i]
        self.cur.items.remove("mna")
        self.cur.cash -= cost
        self.players[o].cash += cost
        self.owner[i] = self.turn
        return True

    def pay_rent(self, i: int) -> int:
        o = self.owner.get(i)
        self.shielded = False
        if o is None or o == self.turn:
            return 0
        if "shield" in self.cur.items:  # 보안 감사 통과: 한 번 면제
            self.cur.items.remove("shield")
            self.shielded = True
            return 0
        r = self.rent_at(i)
        self.cur.cash -= r
        self.players[o].cash += r
        return r

    def settle_debt(self, who: int) -> list[int]:
        """돈이 마이너스면 싼 회사부터 반값에 판다. 그래도 모자라면 파산. 판 칸 목록."""
        p = self.players[who]
        sold: list[int] = []
        for i in sorted((i for i, o in self.owner.items() if o == who), key=lambda i: CELLS[i].price):
            if p.cash >= 0:
                break
            p.cash += (CELLS[i].price + invested(CELLS[i], self.level.get(i, 0))) // 2
            del self.owner[i]
            self.level.pop(i, None)
            sold.append(i)
        if p.cash < 0:
            p.out = True
            for i in [i for i, o in self.owner.items() if o == who]:
                del self.owner[i]
                self.level.pop(i, None)
        return sold

    def next_turn(self) -> None:
        for _ in range(len(self.players)):
            self.turn = (self.turn + 1) % len(self.players)
            if self.turn == 0:
                self.rounds += 1
            if not self.cur.out:
                return

    def over(self) -> bool:
        return len(self.alive()) <= 1 or self.rounds >= MAX_ROUNDS or self.players[0].out

    def ranking(self) -> list[int]:
        return sorted(range(len(self.players)), key=lambda k: (not self.players[k].out, self.assets(k)), reverse=True)

    # ---- 컴퓨터의 판단 ----
    def cpu_buys(self, i: int) -> bool:
        c, p = CELLS[i], self.cur
        same_group = any(self.owner.get(j) == self.turn for j, x in enumerate(CELLS) if x.group == c.group)
        mine = {CELLS[j].art for j, o in self.owner.items() if o == self.turn}
        toward_synergy = any(any(c.art in alts for alts in s.need) and any(any(a in mine for a in alts) for alts in s.need)
                             for s in SYNERGIES)  # 이미 시작한 조합에 들어가는 회사
        keep = 100 if toward_synergy else (120 if same_group else 200)
        return p.cash - self.buy_price(i) >= keep

    def cpu_upgrades(self, i: int) -> bool:
        c, p = CELLS[i], self.cur
        lv = self.level.get(i, 0)
        return p.cash - upgrade_cost(c, lv) >= (150 if self.monopoly(self.turn, c.group) else 350) + (200 if lv >= 2 else 0)

    def cpu_takes_over(self, i: int) -> bool:
        return self.can_takeover(i) and self.cur.cash - self.takeover_cost(i) >= 300


# ---------- 그림 ----------
def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=False, compress_level=1)
    return buf.getvalue()


@lru_cache(maxsize=128)
def _emoji(ch: str, size: int) -> Image.Image:
    font = ImageFont.truetype(EMOJI_FONT, 160)
    im = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
    ImageDraw.Draw(im).text((100, 100), ch, font=font, embedded_color=True, anchor="mm")
    box = im.getbbox()
    if box:
        im = im.crop(box)
    im.thumbnail((size, size), Image.LANCZOS)
    return im


def _paste(base: Image.Image, ch: str, size: int, cx: int, cy: int) -> None:
    e = _emoji(ch, size)
    base.paste(e, (cx - e.width // 2, cy - e.height // 2), e)


@lru_cache(maxsize=256)
def _art(key: str, size: int, round_: bool = True) -> Image.Image | None:
    """assets/tycoon/<key>.webp 를 정사각형으로 (모서리 둥글게). 없으면 None."""
    f = ART / f"{key}.webp"
    if not f.exists():
        return None
    im = Image.open(f).convert("RGBA").resize((size, size), Image.LANCZOS)
    if round_:
        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), size // 5, fill=255)
        im.putalpha(mask)
    return im


def _pic(base: Image.Image, art: str, emoji: str, size: int, cx: int, cy: int) -> None:
    """그림이 있으면 그림, 없으면 이모지."""
    a = _art(art, size)
    if a is not None:
        base.paste(a, (cx - size // 2, cy - size // 2), a)
    else:
        _paste(base, emoji, int(size * 0.8), cx, cy)


def _box(c: int, r: int) -> tuple[int, int, int, int]:
    """화면 전체 캔버스에서 c열 r줄 칸의 자리 (칸 사이 테두리는 비워 둔다)."""
    x, y = c * (SIZE + GAP), r * (SIZE + GAP)
    return x, y, x + SIZE, y + SIZE


WBOX = (WIDE_X, 2 * (SIZE + GAP), WIDE_X + WIDE[0], 2 * (SIZE + GAP) + WIDE[1])


def _shade(c: tuple, k: float) -> tuple:
    return tuple(max(0, min(255, int(v * k))) for v in c)


def _fit(d: _Draw, text: str, size: int, width: int, index: int = 6) -> ImageFont.FreeTypeFont:
    while size > 11 and d.textlength(text, font=_kr(size, index)) > width:
        size -= 1
    return _kr(size, index)


def _ctext(d: _Draw, box: tuple, text: str, size: int, fill, index: int = 6, dy: int = 0) -> None:
    """칸(또는 넓은 칸) 안에 가운데 맞춰 글자. 칸을 넘지 않게 줄인다."""
    x0, y0, x1, y1 = box
    d.text(((x0 + x1) // 2, (y0 + y1) // 2 + dy), text, font=_fit(d, text, size, x1 - x0 - 16, index), fill=fill, anchor="mm")


# 판: 32칸을 8칸씩 네 면으로 나눠, 움직이는 말이 있는 면을 보여준다 (위 줄 → 오른쪽, 아래 줄 ← 로 이어지는 띠).
WINDOW_TILES = [0, 1, 2, 3, 8, 7, 6, 5]


def window_start(focus: int) -> int:
    """판은 8칸씩 네 면. 말이 있는 면을 보여준다: 화면은 가만히 있고 말이 칸을 건너가며,
    면 끝을 넘어가면 다음 면으로 넘긴다."""
    return (focus // 8) * 8


def here_any(tokens: list[int], i: int, g: Game) -> bool:
    return any(pos == i and not g.players[k].out for k, pos in enumerate(tokens))


def draw_cell(g: Game, i: int, tokens: list[int], highlight: int | None, blink: bool) -> bytes:
    img = Image.new("RGB", (SIZE, SIZE), BG)
    d = _Draw(img)
    c = CELLS[i]
    o = g.owner.get(i)
    fill = (30, 33, 46) if c.kind == "company" else (24, 26, 38)
    if o is not None:
        fill = _shade(g.players[o].color, 0.36)
    if highlight == i and blink:
        fill = _shade(fill, 1.8)
    d.rounded_rectangle((3, 3, 193, 193), 18, fill=fill, outline=(255, 255, 255) if highlight == i else None, width=4)
    if c.kind == "company":
        d.rounded_rectangle((3, 3, 193, 17), 8, fill=GROUP_COLORS[c.group])
    _pic(img, c.art, c.emoji, 92, 84 if here_any(tokens, i, g) else 98, 76)
    d.text((98, 140), c.name, font=_fit(d, c.name, 21, 178), fill=INK, anchor="mm")
    if c.kind == "company":
        lv = g.level.get(i, 0)
        sub = f"₩{c.price}" if o is None else f"₩{g.rent_at(i)}" + (" ★" if lv >= MAX_LEVEL else " ▲" * lv)
        d.text((98, 166), sub, font=_fit(d, sub, 19, 178, 6), fill=DIM if o is None else INK, anchor="mm")
    else:
        hint = {"start": t(f"지나면 +{PASS_BONUS}", f"pass +{PASS_BONUS}"), "chance": t("카드 뽑기", "draw a card"),
                "outage": t("한 번 쉬기", "skip a turn"), "bonus": t(f"상금 +{BONUS}", f"prize +{BONUS}"),
                "tax": t(f"정산 -{TAX}", f"pay -{TAX}"), "again": t("한 번 더", "roll again")}[c.kind]
        d.text((98, 166), hint, font=_fit(d, hint, 17, 178, 2), fill=DIM, anchor="mm")
    here = [k for k, pos in enumerate(tokens) if pos == i and not g.players[k].out]
    for m, k in enumerate(here):  # 말은 오른쪽 위 모서리부터 왼쪽으로 (그림을 가리지 않게)
        tx, ty = 168 - m * 40, 34
        a = _art(("token_me", "token_cpu1", "token_cpu2")[k], 38)
        d.ellipse((tx - 21, ty - 21, tx + 21, ty + 21), fill=g.players[k].color, outline=(255, 255, 255), width=3)
        if a is not None:
            img.paste(a, (int(tx - 19), int(ty - 19)), a)
    return _png(img)


def button(label: str, sub: str, color: tuple, enabled: bool = True, emoji: str = "") -> bytes:
    img = Image.new("RGB", (SIZE, SIZE), color if enabled else (40, 42, 54))
    d = _Draw(img)
    if emoji:
        _paste(img, emoji, 64, 98, 70)
    d.text((98, 132 if emoji else 86), label, font=_fit(d, label, 30, 176), fill=(255, 255, 255), anchor="mm")
    if sub:
        d.text((98, 168 if emoji else 128), sub, font=_fit(d, sub, 20, 176, 6), fill=(255, 255, 255) if enabled else DIM, anchor="mm")
    return _png(img)


def _die(s: int, n: int, angle: float) -> Image.Image:
    face = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    fd = ImageDraw.Draw(face)
    fd.rounded_rectangle((0, 0, s - 1, s - 1), int(s * 0.18), fill=(250, 250, 252))
    pips = {1: [(0, 0)], 2: [(-1, -1), (1, 1)], 3: [(-1, -1), (0, 0), (1, 1)], 4: [(-1, -1), (1, -1), (-1, 1), (1, 1)],
            5: [(-1, -1), (1, -1), (0, 0), (-1, 1), (1, 1)], 6: [(-1, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (1, 1)]}[n]
    r = s * 0.09
    for px, py in pips:
        x, y = s / 2 + px * s * 0.26, s / 2 + py * s * 0.26
        fd.ellipse((x - r, y - r, x + r, y + r), fill=(229, 72, 77) if n == 1 else (24, 26, 38))
    return face.rotate(angle, resample=Image.BICUBIC, expand=True)


def _at(img: Image.Image, im: Image.Image, cx: float, cy: float) -> None:
    img.paste(im, (int(cx - im.width / 2), int(cy - im.height / 2)), im)


def _mid(box: tuple) -> tuple[int, int]:
    return (box[0] + box[2]) // 2, (box[1] + box[3]) // 2


def _wrap(d: _Draw, text: str, size: int, width: int, index: int = 6, max_lines: int = 3) -> tuple[list[str], ImageFont.FreeTypeFont]:
    """칸 너비에 맞게 줄을 나눈다 (띄어쓰기 기준). 줄이 넘치면 글자를 줄인다."""
    from i18n import tr
    text = tr(text)  # 그리기 전 번역된 길이로 잰다
    while True:
        font = _kr(size, index)
        lines, cur = [], ""
        for word in text.split():
            nxt = f"{cur} {word}".strip()
            if cur and d.textlength(nxt, font=font) > width:
                lines.append(cur)
                cur = word
            else:
                cur = nxt
        lines.append(cur)
        too_wide = any(d.textlength(ln, font=font) > width for ln in lines)
        if (len(lines) <= max_lines and not too_wide) or size <= 14:
            return lines[:max_lines], font
        size -= 2


def _wide_card(d: _Draw, title: str, lines: list[tuple[str, int, tuple]], bg: tuple, accent: tuple) -> None:
    """연출 글: 아래 줄 왼쪽 세 칸에 한 칸씩 나눠 쓴다 (칸 사이 테두리에 글자가 잘리지 않게).
    첫 칸 = 제목(누가), 둘째 칸 = 첫 줄(핵심), 셋째 칸 = 둘째 줄(설명). 넓은 칸은 연출 중에도 판 지도·돈."""
    parts = [(title, 26, accent)] + list(lines[:2])
    if not title:
        parts = [("", 0, accent)] + list(lines[:2])
    for k in range(3):
        x0, y0, x1, y1 = _box(k, 2)
        d.rounded_rectangle((x0, y0, x1, y1), 22, fill=bg)
        if k >= len(parts) or not parts[k][0]:
            continue
        text, size, color = parts[k]
        rows, font = _wrap(d, text, min(size, 40), x1 - x0 - 20)
        lh = int(font.size * 1.2)
        y = (y0 + y1) // 2 - lh * (len(rows) - 1) // 2
        for ln in rows:
            d.text(((x0 + x1) // 2, y), ln, font=font, fill=color, anchor="mm")
            y += lh


# ---------- 칸마다 어울리는 움직임 (인수·증설·사용료 장면에서 그림 뒤에 그린다) ----------
CELL_FX = {
    "chatbot": "bubbles", "translator": "orbit", "voice": "waves", "imagegen": "sparkle", "videogen": "rays",
    "musicgen": "waves", "recommender": "rays", "search": "rain", "agents": "sparkle", "tutor": "rain",
    "medical": "heartbeat", "drug": "bubbles", "selfdriving": "speed", "drone": "speed", "robotaxi": "speed",
    "robotarm": "gears", "aichip": "circuit", "wearable": "sparkle", "gpucloud": "circuit", "datacenter": "circuit",
    "edge": "waves", "quantum": "orbit", "space": "orbit", "agi": "rays",
    "start": "speed", "conference": "rays", "hackathon": "sparkle", "tax": "rain", "outage": "bubbles", "news": "rays",
}


def _mix(c: tuple, k: float) -> tuple:
    """배경(어두운 색)과 섞인 정도 k (0~1) 로 색을 흐리게 = 투명도 흉내."""
    bg = (8, 9, 14)
    k = max(0.0, min(1.0, k))
    return tuple(int(bg[i] + (c[i] - bg[i]) * k) for i in range(3))


def draw_fx(d: ImageDraw.ImageDraw, kind: str, prog: float, cx: float, cy: float, color: tuple, seed: str) -> None:
    rnd = random.Random(seed)
    tt = prog * 3.0  # 장면 안에서 흐르는 시간
    if kind == "waves":  # 퍼지는 동심원 (소리·전파)
        for k in range(6):
            ph = (tt * 0.9 + k / 6) % 1.0
            r = 90 + ph * 300
            d.ellipse((cx - r, cy - r * 0.7, cx + r, cy + r * 0.7), outline=_mix(color, 1 - ph), width=6)
    elif kind == "bubbles":  # 떠오르는 방울 (대화·약)
        for k in range(26):
            x = cx + rnd.uniform(-330, 330)
            sp = rnd.uniform(0.6, 1.4)
            y = cy + 260 - ((tt * 160 * sp + rnd.uniform(0, 420)) % 420)
            r = rnd.uniform(8, 26)
            d.ellipse((x - r, y - r, x + r, y + r), outline=_mix(color, 0.9), width=4)
    elif kind == "orbit":  # 궤도를 도는 입자 (번역·양자·우주)
        for ring in range(3):
            rx, ry = 150 + ring * 70, 60 + ring * 30
            d.ellipse((cx - rx, cy - ry, cx + rx, cy + ry), outline=_mix(color, 0.35), width=2)
            for k in range(2):
                a = tt * (2.2 - ring * 0.5) + k * math.pi + ring
                x, y = cx + rx * math.cos(a), cy + ry * math.sin(a)
                d.ellipse((x - 11, y - 11, x + 11, y + 11), fill=_mix(color, 1.0))
    elif kind == "sparkle":  # 반짝이는 별 (생성·AGI)
        for k in range(22):
            x, y = cx + rnd.uniform(-360, 360), cy + rnd.uniform(-90, 330)
            tw = (math.sin(tt * 6 + k * 1.7) + 1) / 2
            r = 6 + 20 * tw
            col = _mix(rnd.choice([color, (255, 255, 255), (250, 204, 21)]), 0.4 + 0.6 * tw)
            d.polygon([(x, y - r), (x + r * 0.25, y - r * 0.25), (x + r, y), (x + r * 0.25, y + r * 0.25),
                       (x, y + r), (x - r * 0.25, y + r * 0.25), (x - r, y), (x - r * 0.25, y - r * 0.25)], fill=col)
    elif kind == "rays":  # 돌아가는 빛줄기 (발표·추천·에이전트)
        for k in range(14):
            a = tt * 0.8 + k * (2 * math.pi / 14)
            a2 = a + 0.12
            r = 420
            d.polygon([(cx, cy), (cx + r * math.cos(a), cy + r * math.sin(a)), (cx + r * math.cos(a2), cy + r * math.sin(a2))],
                      fill=_mix(color, 0.28 if k % 2 else 0.16))
    elif kind == "rain":  # 쏟아지는 데이터 조각 (검색·학습)
        for k in range(40):
            x = cx + rnd.uniform(-360, 360)
            y = cy - 120 + ((tt * 260 * rnd.uniform(0.7, 1.3) + rnd.uniform(0, 520)) % 520)
            w = rnd.choice((16, 24, 32))
            d.rounded_rectangle((x, y, x + w, y + 9), 4, fill=_mix(color, rnd.uniform(0.4, 1.0)))
    elif kind == "speed":  # 지나가는 속도선 (자율주행·드론·로켓)
        for k in range(24):
            y = cy + rnd.uniform(-100, 320)
            ln = rnd.uniform(80, 220)
            x = cx + 420 - ((tt * 900 * rnd.uniform(0.8, 1.4) + rnd.uniform(0, 900)) % 900)
            d.line((x, y, x + ln, y), fill=_mix(color, rnd.uniform(0.35, 0.9)), width=rnd.choice((3, 5, 7)))
    elif kind == "circuit":  # 회로를 따라 달리는 불빛 (칩·데이터센터·클라우드)
        for k in range(10):
            a = k * (2 * math.pi / 10) + 0.3
            x1, y1 = cx + 120 * math.cos(a), cy + 90 * math.sin(a)
            x2, y2 = cx + 380 * math.cos(a), cy + 300 * math.sin(a)
            xm = x2
            d.line((x1, y1, xm, y1, x2, y2), fill=_mix(color, 0.35), width=4)
            ph = (tt * 0.9 + k * 0.13) % 1.0
            px = x1 + (xm - x1) * min(1.0, ph * 2)
            py = y1 if ph < 0.5 else y1 + (y2 - y1) * (ph - 0.5) * 2
            d.ellipse((px - 9, py - 9, px + 9, py + 9), fill=_mix((255, 255, 255), 1.0))
            d.ellipse((x2 - 7, y2 - 7, x2 + 7, y2 + 7), fill=_mix(color, 0.8))
    elif kind == "heartbeat":  # 심전도 (의료)
        pts = []
        for k in range(120):
            x = cx - 420 + k * 7
            u = (k / 120 * 3 - tt) % 1.0
            y = cy - (90 if 0.45 < u < 0.5 else -50 if 0.5 <= u < 0.55 else 0)  # 그림 줄을 가로지르게
            pts.append((x, y))
        d.line(pts, fill=_mix(color, 0.95), width=6)
    elif kind == "gears":  # 맞물려 도는 톱니 (로봇)
        for k, (gx, gy, gr, sp) in enumerate(((cx - 260, cy + 160, 80, 1), (cx + 250, cy + 150, 100, -0.8),
                                               (cx - 300, cy - 30, 55, -1.4), (cx + 300, cy - 40, 60, 1.3))):
            teeth = 10
            pts = []
            for j in range(teeth * 2):
                a = tt * sp + j * math.pi / teeth
                r = gr if j % 2 else gr * 0.78
                pts.append((gx + r * math.cos(a), gy + r * math.sin(a)))
            d.polygon(pts, outline=_mix(color, 0.8), width=5)
            d.ellipse((gx - gr * 0.25, gy - gr * 0.25, gx + gr * 0.25, gy + gr * 0.25), outline=_mix(color, 0.8), width=5)


def cell_fx(d: ImageDraw.ImageDraw, cell: Cell, prog: float, cx: float, cy: float) -> None:
    color = GROUP_COLORS[cell.group] if cell.group >= 0 else (234, 179, 8)
    draw_fx(d, CELL_FX.get(cell.art, "sparkle"), prog, cx, cy, color, cell.art)


def cut_frame(kind: str, prog: float, data: dict) -> Image.Image:
    """화면 전체 연출 한 장 (prog 0~1). 그림은 칸 안에, 긴 글은 테두리 없는 넓은 칸에."""
    img = Image.new("RGB", (FW, FH), (8, 9, 14))
    d = _Draw(img)
    color = data.get("color", (229, 72, 77))
    who = data.get("who", "")
    if kind == "dice":
        a, b = data["dice"]
        settle = min(1.0, prog / 0.7)
        for k, n in enumerate((a, b)):
            show = n if settle >= 1 else ((int(prog * 40) + k * 3) % 6) + 1
            tx, ty = _mid(_box(1 + 2 * k, 1))
            bounce = (1 - settle)
            cx = tx + math.sin(prog * 25 + k * 2) * 220 * bounce
            cy = ty - abs(math.sin(prog * 16 + k)) * 230 * bounce
            _at(img, _die(160, show, (1 - settle) * (500 + k * 200)), cx, cy)
        if settle >= 1:
            _ctext(d, _box(2, 1), f"{a + b}", 120, color)
            _wide_card(d, who, [(f"{a} + {b} = {a + b}", 56, INK)], (20, 22, 32), color)
        else:
            _wide_card(d, who, [(t("굴리는 중…", "Rolling…"), 44, DIM)], (20, 22, 32), color)
    elif kind == "card":
        c: Card = data["card"]
        flip = min(1.0, prog / 0.35)
        bx0, by0, _, _ = _box(1, 0)
        _, _, bx1, by1 = _box(3, 1)
        w = int((bx1 - bx0) * abs(math.cos(math.pi * (1 - flip) / 2))) if flip < 1 else bx1 - bx0
        cx = (bx0 + bx1) / 2
        face_up = flip >= 0.5
        d.rounded_rectangle((cx - w / 2, by0, cx + max(w, 4) / 2, by1), 30, fill=(250, 248, 240) if face_up else (60, 50, 120))
        if flip >= 1:
            _pic(img, c.art, c.emoji, 180, *_mid(_box(2, 0)))
            if len(c.desc) <= 8:  # "+200" 처럼 짧으면 크게
                _ctext(d, _box(2, 1), c.desc, 56, (20, 20, 28))
            else:
                _paste(img, "📰", 90, *_mid(_box(2, 1)))
            _wide_card(d, t("깜짝 뉴스", "Breaking news"), [(c.title, 40, (20, 20, 28)), (c.desc, 26, (80, 84, 100))],
                       (250, 248, 240), (190, 130, 0))
        else:
            _paste(img, "📰", 120, *_mid(_box(2, 0)))
            _wide_card(d, t("깜짝 뉴스", "Breaking news"), [("…", 44, DIM)], (20, 22, 32), (234, 179, 8))
    elif kind in ("buy", "upgrade"):
        cell: Cell = data["cell"]
        pop = min(1.0, prog / 0.25)
        cell_fx(d, cell, prog, *_mid(_box(2, 0)))
        _pic(img, cell.art, cell.emoji, int(70 + 120 * pop), *_mid(_box(2, 0)))
        _ctext(d, _box(2, 1), cell.name, 34, INK)
        stamp = t("인수 완료!", "Acquired!") if kind == "buy" else (
            t("유니콘 본사 완공!", "Unicorn HQ built!") if data.get("landmark") else t("서버 증설!", "Scaled up!"))
        _wide_card(d, who, [(stamp if prog > 0.3 else "…", 56, color)], (20, 22, 32), color)
    elif kind == "rent":
        cell = data["cell"]
        amt = data["amount"]
        if int(prog * 8) % 2 == 0 and prog < 0.5:
            d.rectangle((0, 0, FW, FH), fill=(60, 14, 18))
        cell_fx(d, cell, prog, *_mid(_box(2, 0)))
        _pic(img, cell.art, cell.emoji, 180, *_mid(_box(2, 0)))
        payer, payee = data.get("from", ("", color)), data.get("to", ("", color))
        for (name, col), c in ((payer, 1), (payee, 3)):
            x0, y0, x1, y1 = _box(c, 1)
            d.ellipse((x0 + 58, y0 + 30, x0 + 138, y0 + 110), fill=col, outline=(255, 255, 255), width=4)
            _ctext(d, (x0, y0 + 110, x1, y1), name, 26, INK)
        _ctext(d, _box(2, 1), "→", 90, (229, 72, 77))
        shown = int(amt * min(1.0, prog / 0.5))
        _wide_card(d, t("API 사용료", "API fee"), [(f"₩{shown}", 80, (229, 72, 77))], (20, 22, 32), DIM)
    elif kind == "synergy":
        syn: Synergy = data["synergy"]
        _paste(img, syn.emoji, int(120 + 60 * min(1.0, prog * 2)), *_mid(_box(2, 0)))
        draw_fx(d, "sparkle", prog, *_mid(_box(2, 0)), (250, 204, 21), syn.key)
        _paste(img, syn.emoji, int(120 + 60 * min(1.0, prog * 2)), *_mid(_box(2, 0)))
        _ctext(d, _box(2, 1), syn.name, 30, (250, 204, 21))
        _wide_card(d, t(f"{who} · 시너지 달성!", f"{who} · Synergy!"), [(syn.name, 44, (250, 204, 21)), (syn.effect, 26, INK)],
                   (20, 22, 32), color)
    elif kind == "shield":
        _paste(img, "🛡️", int(120 + 50 * min(1.0, prog * 2)), *_mid(_box(2, 0)))
        _wide_card(d, who, [(t("보안 감사 통과: 사용료 면제!", "Security audit: fee waived!"), 40, (46, 196, 160))],
                   (20, 22, 32), color)
    elif kind == "takeover":
        cell = data["cell"]
        cell_fx(d, cell, prog, *_mid(_box(2, 0)))
        _pic(img, cell.art, cell.emoji, 180, *_mid(_box(2, 0)))
        _paste(img, "🤝", 100, *_mid(_box(2, 1)))
        _wide_card(d, who, [(t("인수합병 성공!", "M&A done!"), 48, color), (data.get("line", ""), 26, INK)],
                   (20, 22, 32), color)
    elif kind == "pass":
        _pic(img, data.get("art", "start"), data.get("emoji", "🚀"), int(120 + 50 * min(1.0, prog * 2)), *_mid(_box(2, 0)))
        _paste(img, "💰", 110, *_mid(_box(2, 1)))
        _wide_card(d, who, [(data.get("text") or t(f"투자 라운드 +{PASS_BONUS}", f"Funding +{PASS_BONUS}"), 48, (46, 196, 160))],
                   (20, 22, 32), color)
    elif kind == "outage":
        if int(prog * 10) % 2 == 0:
            d.rectangle((0, 0, FW, FH), fill=(40, 10, 10))
        _pic(img, "outage", "🔥", 190, *_mid(_box(2, 0)))
        for c in (1, 3):
            _paste(img, "🔥", 120, *_mid(_box(c, 1)))
        _wide_card(d, t("서버 장애!", "Outage!"), [(t("한 번 쉬어요", "Skip a turn"), 48, (255, 140, 90))], (30, 12, 12), (255, 140, 90))
    elif kind == "bankrupt":
        _paste(img, "📉", 160, *_mid(_box(2, 0)))
        _wide_card(d, "", [(data.get("line", ""), 52, (229, 72, 77))], (30, 12, 16), (229, 72, 77))
    elif kind in ("win", "lose"):
        rk: list = data["ranking"]
        win = kind == "win"
        _pic(img, "win" if win else "lose", "🏆" if win else "😵", 190, *_mid(_box(2, 0)))
        for place, (c, medal) in enumerate(((2, "🥇"), (1, "🥈"), (3, "🥉"))):
            if place < len(rk):
                name, value, col = rk[place]
                x0, y0, x1, y1 = _box(c, 1)
                _paste(img, medal, 70, (x0 + x1) // 2 - 36, y0 + 50)
                tok = _art(("token_me", "token_cpu1", "token_cpu2")[data.get("who_idx", [0, 1, 2])[place]], 76)
                if tok is not None:
                    img.paste(tok, ((x0 + x1) // 2 + 2, y0 + 12), tok)
                _ctext(d, (x0, y0 + 100, x1, y0 + 140), name, 28, col)
                _ctext(d, (x0, y0 + 140, x1, y1), f"₩{value}", 26, INK)
        _wide_card(d, t("게임 끝", "Game over"),
                   [(t("유니콘 등극!", "You're a unicorn!") if win else t("다음 라운드를 노려요", "Better luck next time"), 46,
                     (234, 179, 8) if win else DIM)], (20, 22, 32), DIM)
    elif kind == "title":
        _pic(img, "title", "🦄", int(150 + 40 * min(1.0, prog * 2)), *_mid(_box(2, 0)))
        _paste(img, "🎲", 110, *_mid(_box(1, 1)))
        _paste(img, "💰", 110, *_mid(_box(3, 1)))
        _paste(img, "🤖", 110, *_mid(_box(2, 1)))
        _wide_card(d, t("AI 스타트업 타이쿤", "AI Startup Tycoon"),
                   [(t("AI 회사를 인수하고", "Buy AI companies,"), 30, INK), (t("API 사용료를 받으세요", "collect API fees"), 30, INK)],
                   (20, 22, 32), (234, 179, 8))
    return img


def split_full(img: Image.Image) -> tuple[dict[int, bytes], bytes]:
    out = {}
    for r in range(3):
        for c in range(5 if r < 2 else 3):
            out[r * 5 + c] = _png(img.crop(_box(c, r)))
    return out, _png(img.crop(WBOX))


# ---------- 진행 ----------
CUT_LEN = {"dice": 1.4, "card": 2.2, "buy": 1.3, "upgrade": 1.3, "rent": 1.6, "pass": 1.1, "outage": 1.5,
           "bankrupt": 1.8, "title": 1.6, "win": 6.0, "lose": 6.0, "synergy": 2.2, "shield": 1.4, "takeover": 1.8}
STEP = 0.22  # 말이 한 칸 가는 시간


class Tycoon:
    """games.Games 가 부르는 게임 하나 (던전과 같은 꼴: start · press · tick · tiles · wide · fullscreen)."""

    def __init__(self, save_path: Path | None = None, seed: int | None = None) -> None:
        self.save_path = save_path
        self.seed = seed
        self.stats = self._load()
        self.g = Game(random.Random(seed))
        self.phase = "title"   # title | roll | choose | busy | over
        self.choice: tuple[str, int] | None = None  # ("buy"|"upgrade", 칸)
        self.queue: list[tuple[float, Callable[[], None]]] = []
        self.cut: tuple[str, float, dict] | None = None
        self.tokens = [0, 0, 0]
        self.log = ("", "")
        self.highlight: int | None = None
        self.ver = 0
        self._cache: tuple | None = None
        self._wide: bytes = b""
        self.fast = False  # 테스트: 연출 없이 바로

    # ---- 저장 ----
    def _load(self) -> dict:
        try:
            return json.loads(self.save_path.read_text()) if self.save_path else {}
        except Exception:
            return {}

    def _save(self) -> None:
        if not self.save_path:
            return
        try:
            self.save_path.parent.mkdir(parents=True, exist_ok=True)
            self.save_path.write_text(json.dumps(self.stats))
        except OSError:
            pass

    # ---- 게임 메뉴 연결 ----
    def menu_tile(self) -> bytes:
        img = Image.new("RGB", (SIZE, SIZE), (24, 26, 38))
        d = _Draw(img)
        _pic(img, "title", "🦄", 120, 98, 96)
        d.text((98, 22), t("AI 타이쿤", "AI Tycoon"), font=_kr(24, 6), fill=INK, anchor="mm")
        w = self.stats.get("wins", 0)
        d.text((98, 172), t(f"우승 {w}번", f"{w} wins") if w else t("새 게임", "New game"), font=_kr(20, 4), fill=DIM, anchor="mm")
        return _png(img)

    def suspend(self) -> None:
        pass

    @property
    def active(self) -> bool:
        return bool(self.queue) or self.cut is not None

    @property
    def fullscreen(self) -> bool:
        return self.cut is not None

    def key(self) -> tuple:
        return (self.ver, self._frame(time.time()))

    def _frame(self, now: float) -> int:
        return int((now - self.cut[1]) * FPS) if self.cut else 0

    def _bump(self) -> None:
        self.ver += 1

    def _later(self, delay: float, fn: Callable[[], None]) -> float:
        at = (self.queue[-1][0] if self.queue else time.time()) + (0 if self.fast else delay)
        self.queue.append((at, fn))
        return at

    def _show(self, kind: str, data: dict | None = None) -> None:
        """연출 하나를 대기열에 넣는다 (앞 연출이 끝난 뒤 시작)."""
        dur = CUT_LEN[kind]
        data = data or {}
        if self.fast:
            return
        self._later(0.0, lambda: self._begin(kind, data))
        self._later(dur, self._end_cut)

    SOUND = {"dice": "card", "card": "flip", "buy": "coin", "upgrade": "levelup", "rent": "bad", "pass": "win",
             "outage": "boom", "bankrupt": "dead", "win": "jackpot", "lose": "lose", "title": "intro",
             "synergy": "jackpot", "shield": "levelup", "takeover": "coin"}

    def _begin(self, kind: str, data: dict) -> None:
        play(self.SOUND[kind], 0)
        self.cut = (kind, time.time(), data)
        self._bump()

    def _end_cut(self) -> None:
        self.cut = None
        self._bump()

    # ---- 시작 ----
    def start(self) -> None:
        self.g = Game(random.Random(self.seed))
        self.tokens = [0, 0, 0]
        self.queue, self.cut, self.choice, self.highlight = [], None, None, None
        self.log = (t("주사위를 굴려 시작하세요", "Roll to start"), t("오른쪽 위 버튼", "Top-right button"))
        self.phase = "busy"
        self._show("title")
        self._later(0.0, self._my_turn_or_cpu)
        self._bump()

    # ---- 입력 ----
    def press(self, idx: int, now: float) -> None:
        if self.cut is not None and self.cut[0] in ("win", "lose") and now - self.cut[1] > 1.5:
            self.queue, self.cut = [], None
            self.start()
            return
        if self.phase == "over":
            self.start()
            return
        g = self.g
        if self.phase in ("roll", "pivot") and idx == BTN_A:
            self._turn()
        elif self.phase == "roll" and idx == BTN_B and "pivot" in g.cur.items:
            self.phase = "pivot"  # 피벗: 보이는 8칸 중 하나를 누르면 그 칸으로
            self.log = (t("피벗: 갈 칸을 누르세요", "Pivot: tap a cell"), t("오른쪽 아래: 취소", "Bottom right: cancel"))
        elif self.phase == "pivot" and idx == BTN_B:
            self.phase = "roll"
            self.log = (t("내 차례", "Your turn"), t("오른쪽 위 굴리기를 누르세요", "Press Roll (top right)"))
        elif self.phase == "pivot" and idx in WINDOW_TILES:
            target = (window_start(self.tokens[g.turn]) + WINDOW_TILES.index(idx)) % N
            if target != g.cur.pos:
                g.cur.items.remove("pivot")
                self.phase = "busy"
                g.move_to(target, passing=False)
                self.tokens[g.turn] = target
                self.log = (t("피벗!", "Pivot!"), CELLS[target].name)
                self._later(0.3, lambda: self._arrive(target))
        elif self.phase == "choose" and self.choice and idx in (BTN_A, BTN_B):
            kind, i = self.choice
            self.choice = None
            self.phase = "busy"
            if idx == BTN_A:
                self._do_choice(kind, i)
            elif kind == "takeover":  # 인수합병을 안 쓰면 사용료를 낸다
                self._pay(i)
                self._bump()
                return
            self._later(0.3, self._end_turn)
        self._bump()

    # ---- 한 차례 ----
    def _my_turn_or_cpu(self) -> None:
        g = self.g
        if g.over():
            self._finish()
            return
        p = g.cur
        if p.skip:
            p.skip = False
            self.log = (t(f"{p.name}: 서버 복구 중", f"{p.name}: restoring servers"), t("이번 차례는 쉬어요", "Skips this turn"))
            self._bump()
            self._later(1.0, self._end_turn)
            return
        if "agi" in g.synergies(g.turn):
            p.cash += AGI_GRANT
        if p.cpu:
            self.phase = "busy"
            self.log = (t(f"{p.name} 차례", f"{p.name}'s turn"), t("생각하는 중…", "Thinking…"))
            self._bump()
            self._later(0.7, self._turn)
        else:
            self.phase = "roll"
            self.log = (t("내 차례", "Your turn"), t("오른쪽 위 굴리기를 누르세요", "Press Roll (top right)"))
            self._bump()

    def _turn(self) -> None:
        g = self.g
        self.phase = "busy"
        a, b = g.roll()
        who = g.cur.name
        self._show("dice", {"dice": (a, b), "who": who, "color": g.cur.color})
        steps = g.path(a + b)
        for k, pos in enumerate(steps):
            self._later(STEP if k else 0.1, lambda pos=pos: self._step(pos))
        self._later(0.2, lambda: self._arrive(steps[-1]))

    def _step(self, pos: int) -> None:
        g = self.g
        me = g.turn
        passed = g.move_to(pos)
        self.tokens[me] = pos
        if not self.fast:
            play("tick", 0)
        if passed:
            self.log = (t(f"{g.cur.name}: 투자 라운드 +{PASS_BONUS}", f"{g.cur.name}: funding +{PASS_BONUS}"), "")
            if not self.fast:
                play("coin", 0)
        self._bump()

    def _arrive(self, pos: int) -> None:
        g = self.g
        c = CELLS[pos]
        self.highlight = pos
        p = g.cur
        if c.kind == "company":
            o = g.owner.get(pos)
            if o is None:
                price = g.buy_price(pos)
                if p.cash >= price:
                    self._offer("buy", pos)
                    return
                self.log = (c.name, t(f"₩{price} · 돈이 모자라요", f"₩{price} · not enough cash"))
            elif o == g.turn:
                lv = g.level.get(pos, 0)
                cost = upgrade_cost(c, lv)
                if lv >= MAX_LEVEL:
                    self.log = (c.name, t("유니콘 본사 · 최대 단계예요", "Unicorn HQ · max level"))
                elif p.cash >= cost:
                    self._offer("upgrade", pos)
                    return
                else:
                    what = t("유니콘 본사", "Unicorn HQ") if lv >= 2 else t("증설", "Scale-up")
                    self.log = (c.name, t(f"{what} ₩{cost} · 돈이 모자라요", f"{what} ₩{cost} · not enough cash"))
            elif g.can_takeover(pos):  # 인수합병 제안서가 있으면 사용료 대신 사 올지 묻는다
                self._offer("takeover", pos)
                return
            else:
                self._pay(pos)
                return
        elif c.kind == "chance":
            card = g.draw()
            self._show("card", {"card": card})
            self._later(0.0, lambda: self._apply_card(card))
            return
        elif c.kind == "bonus":
            prize = BONUS * 2 if "health" in g.synergies(g.turn) else BONUS  # 헬스케어 AI 시너지: 2배
            p.cash += prize
            self.log = (t(f"{p.name}: AI 학회 발표 상금 +{prize}", f"{p.name}: summit prize +{prize}"), c.name)
            self._show("pass", {"who": p.name, "color": p.color, "text": t(f"학회 상금 +{prize}", f"Prize +{prize}"), "art": "conference", "emoji": "🎤"})
        elif c.kind == "tax":
            if "vertical" in g.synergies(g.turn):  # 수직 계열화 시너지: 정산 면제
                self.log = (t(f"{p.name}: 수직 계열화로 정산 면제", f"{p.name}: vertical stack, no bill"), c.name)
            else:
                p.cash -= TAX
                self.log = (t(f"{p.name}: 클라우드 정산 -{TAX}", f"{p.name}: cloud bill -{TAX}"), c.name)
                self._later(0.0, self._check_debt)
        elif c.kind == "again":
            self.log = (t(f"{p.name}: 해커톤! 한 번 더", f"{p.name}: hackathon! roll again"), c.name)
            self._bump()
            self._later(0.8, self._my_turn_or_cpu)  # 차례를 넘기지 않음
            return
        elif c.kind == "outage":
            p.skip = True
            self._show("outage")
            self.log = (t(f"{p.name}: 서버 장애", f"{p.name}: outage"), t("다음 차례 쉬기", "Skips next turn"))
        else:
            self.log = (c.name, t("투자자들이 반겨요", "Investors cheer"))
        self._bump()
        self._later(0.6, self._end_turn)

    def _pay(self, pos: int) -> None:
        """남의 회사에 섰을 때: 사용료 (면제권이 있으면 면제). 차례를 끝낸다."""
        g = self.g
        c, p, o = CELLS[pos], g.cur, g.owner.get(pos)
        amt = g.pay_rent(pos)
        owner = g.players[o].name if o is not None else ""
        if g.shielded:
            self._show("shield", {"who": p.name, "color": p.color})
            self.log = (t("보안 감사 통과: 사용료 면제", "Security audit: fee waived"), c.name)
        else:
            line = t(f"{p.name} → {owner}", f"{p.name} → {owner}")
            self._show("rent", {"cell": c, "amount": amt, "line": line,
                                "from": (p.name, p.color), "to": (owner, g.players[o].color)})
            self.log = (t(f"API 사용료 ₩{amt}", f"API fee ₩{amt}"), line)
            self._later(0.0, self._check_debt)
        self._bump()
        self._later(0.6, self._end_turn)

    def _cost(self, kind: str, pos: int) -> int:
        g = self.g
        return {"buy": g.buy_price(pos), "upgrade": upgrade_cost(CELLS[pos], g.level.get(pos, 0)),
                "takeover": g.takeover_cost(pos)}[kind]

    def _apply_card(self, card: Card) -> None:
        g = self.g
        before = g.cur.pos
        g.apply_card(card)
        self.log = (card.title, card.desc if card.kind != "keep" else t("보관함에 넣었어요", "Kept for later"))
        if g.cur.pos != before:  # 이동 카드: 말 옮기고 도착 칸 처리
            self.tokens[g.turn] = g.cur.pos
            if card.kind == "goto_start":
                self._show("pass", {"who": g.cur.name, "color": g.cur.color})
                self._later(0.5, self._end_turn)
            elif card.kind == "outage":
                self._later(0.5, self._end_turn)
            else:
                self._later(0.4, lambda: self._arrive(g.cur.pos))
            self._bump()
            return
        self._bump()
        self._later(0.0, self._check_debt)
        self._later(0.6, self._end_turn)

    def _offer(self, kind: str, pos: int) -> None:
        g = self.g
        c = CELLS[pos]
        cost = self._cost(kind, pos)
        landmark = kind == "upgrade" and g.level.get(pos, 0) >= 2
        if g.cur.cpu:
            yes = {"buy": g.cpu_buys, "upgrade": g.cpu_upgrades, "takeover": g.cpu_takes_over}[kind](pos)
            done = {"buy": t(f"₩{cost} 인수", f"buys for ₩{cost}"), "upgrade": t(f"₩{cost} 증설", f"scales up ₩{cost}"),
                    "takeover": t(f"₩{cost} 인수합병", f"M&A ₩{cost}")}[kind]
            self.log = (c.name, done if yes else t("넘어가요", "passes"))
            self._bump()
            if yes:
                self._later(0.5, lambda: self._do_choice(kind, pos))
            elif kind == "takeover":
                self._later(0.5, lambda: self._pay(pos))
                return
            self._later(0.8, self._end_turn)
            return
        self.choice = (kind, pos)
        self.phase = "choose"
        ask = {"buy": t(f"₩{cost} 에 인수할까요?", f"Buy for ₩{cost}?"),
               "upgrade": t(f"₩{cost} 에 유니콘 본사를 지을까요?", f"Build Unicorn HQ for ₩{cost}?") if landmark
               else t(f"₩{cost} 에 서버를 증설할까요?", f"Scale up for ₩{cost}?"),
               "takeover": t(f"인수합병 제안서로 ₩{cost} 에 사 올까요? (아니면 사용료)", f"Use M&A offer for ₩{cost}? (else pay fee)")}[kind]
        self.log = (c.name, ask)
        self._bump()

    def _do_choice(self, kind: str, pos: int) -> None:
        g = self.g
        before = set(g.synergies(g.turn))
        owner = g.owner.get(pos)
        ok = {"buy": g.buy, "upgrade": g.upgrade, "takeover": g.takeover}[kind](pos)
        if ok:
            data = {"cell": CELLS[pos], "who": g.cur.name, "color": g.cur.color,
                    "landmark": kind == "upgrade" and g.level.get(pos, 0) >= MAX_LEVEL}
            if kind == "takeover" and owner is not None:
                data["line"] = t(f"{g.players[owner].name} 에게서 {CELLS[pos].name}", f"{CELLS[pos].name} from {g.players[owner].name}")
            self._show(kind, data)
            for key in [k for k in g.synergies(g.turn) if k not in before]:  # 새로 갖춘 시너지
                self._show("synergy", {"synergy": SYN[key], "who": g.cur.name, "color": g.cur.color})
                self.log = (t(f"{g.cur.name}: 시너지 {SYN[key].name}", f"{g.cur.name}: synergy {SYN[key].name}"), SYN[key].effect)
        self._bump()

    def _check_debt(self) -> None:
        g = self.g
        for k, p in enumerate(g.players):
            if p.cash < 0 and not p.out:
                sold = g.settle_debt(k)
                if p.out:
                    self._show("bankrupt", {"line": t(f"{p.name} 파산", f"{p.name} went bust")})
                elif sold:
                    self.log = (t(f"{p.name}: 회사 매각", f"{p.name}: sold companies"),
                                ", ".join(CELLS[i].name for i in sold))
        self._bump()

    def _end_turn(self) -> None:
        g = self.g
        self.highlight = None
        if g.over():
            self._finish()
            return
        g.next_turn()
        self._bump()
        self._later(0.2, self._my_turn_or_cpu)

    def _finish(self) -> None:
        g = self.g
        rk = g.ranking()
        win = rk[0] == 0 and not g.players[0].out
        self.stats["plays"] = self.stats.get("plays", 0) + 1
        if win:
            self.stats["wins"] = self.stats.get("wins", 0) + 1
        self._save()
        self.phase = "over"
        rows = [(g.players[k].name, g.assets(k) if not g.players[k].out else 0, g.players[k].color) for k in rk]
        self.log = (t("게임 끝", "Game over"), t("아무 칸: 새 게임", "Any key: new game"))
        self._show("win" if win else "lose", {"ranking": rows, "who_idx": rk})
        self._bump()

    # ---- 시간 ----
    def tick(self, now: float) -> None:
        while self.queue and self.queue[0][0] <= now:
            _, fn = self.queue.pop(0)
            fn()
            if self.fast:
                continue

    def run_all(self) -> None:
        """테스트: 연출 없이 끝까지 (내 차례는 자동으로 굴리고 살 수 있으면 산다)."""
        self.fast = True
        for _ in range(5000):
            if self.phase == "over":
                return
            if self.queue:
                self.tick(float("inf"))
                continue
            if self.phase == "roll":
                self._turn()
            elif self.phase == "choose":
                self.press(BTN_A, time.time())
            else:
                break

    # ---- 그리기 ----
    def tiles(self) -> dict[int, bytes]:
        now = time.time()
        k = (self.ver, self._frame(now))
        if self._cache and self._cache[0] == k:
            return self._cache[1]
        if self.cut is not None:
            kind, t0, data = self.cut
            prog = min(1.0, (now - t0) / CUT_LEN[kind])
            out, _ = split_full(cut_frame(kind, prog, data))
            self._wide = self._board_wide()  # 연출 중에도 넓은 칸은 판 지도·돈
        else:
            blink = False  # 깜빡이면 매번 판 전체를 다시 보내야 해서 끔 (테두리로만 표시)
            start = window_start(self.tokens[self.g.turn])
            out = {tile: draw_cell(self.g, (start + n) % N, self.tokens, self.highlight, blink)
                   for n, tile in enumerate(WINDOW_TILES)}
            out[BTN_A], out[BTN_B] = self._buttons()
        self._cache = (k, out)
        return out

    def _buttons(self) -> tuple[bytes, bytes]:
        g = self.g
        me = g.players[0]
        if self.phase == "roll":
            second = (button(t("피벗", "Pivot"), t("보이는 칸으로", "jump to a cell"), (124, 58, 237), True, "🔀")
                      if "pivot" in me.items else button(f"₩{me.cash}", t("내 돈", "Cash"), (40, 42, 54), False))
            return button(t("굴리기", "Roll"), "", PLAYER_COLORS[0], True, "🎲"), second
        if self.phase == "pivot":
            return (button(t("굴리기", "Roll"), t("피벗 안 씀", "skip pivot"), PLAYER_COLORS[0], True, "🎲"),
                    button(t("취소", "Cancel"), t("칸을 누르면 이동", "tap a cell to jump"), (70, 72, 90), True))
        if self.phase == "choose" and self.choice:
            kind, i = self.choice
            cost = self._cost(kind, i)
            label = {"buy": t("인수", "Buy"), "upgrade": t("본사 짓기", "Build HQ") if self.g.level.get(i, 0) >= 2
                     else t("증설", "Scale up"), "takeover": t("인수합병", "M&A")}[kind]
            no = t("사용료 내기", "Pay fee") if kind == "takeover" else t("넘어가기", "Pass")
            return (button(label, f"₩{cost}", (46, 160, 110), True, "✍️"),
                    button(no, f"₩{me.cash}", (70, 72, 90), True))
        if self.phase == "over":
            return (button(t("새 게임", "New game"), "", PLAYER_COLORS[0], True, "🔁"),
                    button(t("메뉴", "Menu"), t("가운데 버튼", "middle key"), (70, 72, 90), False))
        cur = g.cur
        return (button(cur.name, t("차례", "turn"), _shade(cur.color, 0.6), False, "⏳"),
                button(f"₩{me.cash}", t("내 돈", "Cash"), (40, 42, 54), False))

    def wide(self) -> bytes:
        return self._board_wide()

    def _board_wide(self) -> bytes:
        """넓은 칸: 판 전체 지도 + 소식 + 세 사람의 돈 + 내 보관 카드·시너지 (연출 중에도 그대로)."""
        g = self.g
        img = Image.new("RGB", WIDE, (13, 14, 20))
        d = _Draw(img)
        # 왼쪽: 판 전체 작은 지도 (10×8 테두리 = 32칸), 주인 색 + 말 위치
        cs, ox, oy = 17, 12, 22
        ring = [(x, 0) for x in range(10)] + [(9, y) for y in range(1, 8)] + [(x, 7) for x in range(8, -1, -1)] + \
               [(0, y) for y in range(6, 0, -1)]
        shown = window_start(self.tokens[g.turn])
        for i, (gx, gy) in enumerate(ring):
            x, y = ox + gx * cs, oy + gy * cs
            o = g.owner.get(i)
            c = CELLS[i]
            col = g.players[o].color if o is not None else ((52, 56, 74) if c.kind == "company" else (34, 36, 50))
            on = shown <= i < shown + 8  # 지금 덱에 보이는 면
            d.rounded_rectangle((x + 1, y + 1, x + cs - 1, y + cs - 1), 3, fill=col,
                                outline=(255, 255, 255) if on else None, width=1)
        for k, pos in enumerate(self.tokens):
            if g.players[k].out:
                continue
            gx, gy = ring[pos]
            x, y = ox + gx * cs + cs // 2 + (k - 1) * 4, oy + gy * cs + cs // 2
            d.ellipse((x - 5, y - 5, x + 5, y + 5), fill=g.players[k].color, outline=(255, 255, 255), width=2)
        d.text((ox + 5 * cs, oy + 4 * cs), t(f"{min(g.rounds + 1, MAX_ROUNDS)}/{MAX_ROUNDS}턴", f"turn {min(g.rounds + 1, MAX_ROUNDS)}/{MAX_ROUNDS}"),
               font=_kr(15, 4), fill=DIM, anchor="mm")
        # 오른쪽: 소식 + 세 사람
        tx = 196
        big, small = self.log
        d.text((tx, 24), big, font=_fit(d, big, 21, WIDE[0] - tx - 12), fill=INK, anchor="lm")
        if small:
            d.text((tx, 50), small, font=_fit(d, small, 16, WIDE[0] - tx - 12, 2), fill=DIM, anchor="lm")
        for k, p in enumerate(g.players):
            y = 92 + k * 34
            if g.turn == k and self.phase != "over":
                d.rounded_rectangle((tx - 8, y - 15, WIDE[0] - 6, y + 15), 10, fill=(30, 33, 48))
            d.ellipse((tx, y - 8, tx + 16, y + 8), fill=p.color)
            d.text((tx + 24, y), p.name, font=_fit(d, p.name, 18, 110), fill=DIM if p.out else INK, anchor="lm")
            d.text((WIDE[0] - 14, y), t("파산", "bust") if p.out else f"₩{p.cash}", font=_kr(20, 6),
                   fill=(229, 72, 77) if p.out or p.cash < 150 else INK, anchor="rm")
        # 맨 아래: 내 보관 카드와 시너지 (글자로: 이모지는 글꼴에 없음)
        me = g.players[0]
        parts = [ITEMS[k][1] for k in me.items] + [SYN[k].name for k in g.synergies(0)]
        if parts:
            line = t("보관·시너지: ", "Kept/synergy: ") + " · ".join(parts)
            d.text((12, WIDE[1] - 14), line, font=_fit(d, line, 15, WIDE[0] - 24, 4), fill=(250, 204, 21), anchor="lm")
        return _png(img)
