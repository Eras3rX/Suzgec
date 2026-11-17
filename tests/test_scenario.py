"""Сценарий: «Сколько человек в списке? Список: Али Алиев, Маqa Алиев, Ильхам Мурадов».

Запрос уходит в AI замаскированным, ответ AI («В списке 3 человека: Ad1, Ad2, Ad3») восстанавливается,
пользователь получает ответ с настоящими именами. Только синтетические данные.
"""
import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.detector import detect_rules
from app.main import app
from app.masker import mask, restore_tolerant

client = TestClient(app)

RU = "Сколько человек в списке? Список: Али Алиев, Маqa Алиев, Ильхам Мурадов"  # «q» латинская
AZ = "Neçə nəfər var? Siyahı: Əli Əliyev, Maqa Əliyev, İlham Muradov"
NAMES_RU = ["Али Алиев", "Маqa Алиев", "Ильхам Мурадов"]
NAMES_AZ = ["Əli Əliyev", "Maqa Əliyev", "İlham Muradov"]


def names(text):
    return [f.value for f in detect_rules(text) if f.category == "AD"]


# ---------- Поиск имён: имя + фамилия, даже если имени нет в словаре ----------


def test_cyrillic_list_of_names_is_fully_found():
    assert names(RU) == NAMES_RU


def test_azerbaijani_list_of_names_is_fully_found():
    assert names(AZ) == NAMES_AZ


def test_unknown_given_name_before_a_surname_is_part_of_the_name():
    assert names("Müştəri Aytac Hüseynova zəng etdi.") == ["Aytac Hüseynova"]
    assert names("Клиент Фарухшат Рзаев подал жалобу.") == ["Фарухшат Рзаев"]


@pytest.mark.parametrize("text", [
    "Город Ростов большой. Основа договора простая.",   # города и обычные слова на -ов/-ова
    "Совещание в Ростове прошло. Алиев пришёл вовремя.",  # одинокая кириллическая фамилия: не берём
    "Президент Алиев выступил.",                          # должность — не имя
    "Cənab Həsənov gəldi, direktor Həsənov da.",           # «Cənab»/«Direktor» — не имена
    "Bu Yeni Sürətli xidmət.",                            # слабое окончание -li без имени
    "Мы Иванов не знаем.",                                # слишком короткое слово
])
def test_no_false_names(text):
    assert names(text) in ([], ["Həsənov", "Həsənov"])  # сильная фамилия -ov сама по себе находится


def test_title_word_is_not_swallowed_into_the_name():
    assert names("Cənab Həsənov gəldi.") == ["Həsənov"]


def test_sentence_initial_word_is_not_taken_as_a_name():
    assert names("Сегодня Алиев пришёл.") == []
    assert names("Bu gün Əliyev gəldi.") == ["Əliyev"]  # Əliyev — сильная фамилия, «Bu gün» не имя


# ---------- Восстановление ответа AI, который исказил метки ----------

MAPPING = {"[AD_1]": "Али Алиев", "[AD_2]": "Маqa Алиев", "[AD_3]": "Ильхам Мурадов"}
ALL = "Али Алиев, Маqa Алиев, Ильхам Мурадов"


@pytest.mark.parametrize("answer,fuzzy", [
    ("В списке 3 человека: [AD_1], [AD_2], [AD_3]", 0),
    ("В списке 3 человека: Ad1, Ad2, Ad3", 3),         # пример пользователя
    ("В списке 3 человека: AD_1, AD_2, AD_3", 3),
    ("В списке 3 человека: ad-1, AD 2 нет, AD3", None),  # смесь, проверяется отдельно
    ("В списке 3 человека: [ad_1], [Ad 2], [ AD-3 ]", 3),
])
def test_altered_labels_are_restored(answer, fuzzy):
    restored, info = restore_tolerant(answer, MAPPING)
    if fuzzy is None:
        assert "Али Алиев" in restored
        return
    assert restored == f"В списке 3 человека: {ALL}"
    assert info.fuzzy == fuzzy and info.missing == []


def test_exact_labels_count_as_exact():
    restored, info = restore_tolerant("[AD_1] и [AD_2]", MAPPING)
    assert restored == "Али Алиев и Маqa Алиев"
    assert (info.exact, info.fuzzy, info.missing) == (2, 0, ["[AD_3]"])


def test_unknown_or_lookalike_words_are_left_alone():
    text = "Версия abc1, номер AD11, метка AD_9, файл ad1x, [FIN_1]"
    restored, info = restore_tolerant(text, {"[AD_1]": "Имя"})
    assert restored == text  # «AD11» — не «AD_1» и не «AD_1»+1; чужих меток не трогаем
    assert info.exact == info.fuzzy == 0


def test_label_inside_restored_value_is_not_reprocessed():
    restored, _ = restore_tolerant("[AD_1]", {"[AD_1]": "Мой код AD_1", "[AD_2]": "x"})
    assert restored == "Мой код AD_1"


# ---------- Сквозной сценарий через /api/chat ----------


def run_chat(monkeypatch, text, ai_answer):
    """Отправить text, подменив настоящий AI ответом ai_answer. Возвращает (ответ API, что получил AI)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    received = []

    def fake_ai(masked_text):
        received.append(masked_text)
        return ai_answer

    monkeypatch.setattr(main, "ask_llm", fake_ai)
    r = client.post("/api/chat", json={"text": text})
    assert r.status_code == 200, r.text
    return r.json(), received


@pytest.mark.parametrize("text,names_in_text", [(RU, NAMES_RU), (AZ, NAMES_AZ)])
def test_end_to_end_names_never_reach_ai_and_come_back(monkeypatch, text, names_in_text):
    body, received = run_chat(monkeypatch, text, "В списке 3 человека: [AD_1], [AD_2], [AD_3]")
    assert len(received) == 1
    for name in names_in_text:  # AI не видит ни имён, ни их частей
        for part in name.split():
            assert part not in received[0], part
    assert received[0].count("[AD_") == 3
    assert body["answer"] == "В списке 3 человека: " + ", ".join(names_in_text)
    assert body["warnings"] == []


def test_end_to_end_with_ai_that_rewrites_labels_like_ad1(monkeypatch):
    """AI ответил «Ad1, Ad2, Ad3» вместо [AD_1]…: имена всё равно возвращаются, пользователь видит предупреждение."""
    body, _ = run_chat(monkeypatch, RU, "В списке 3 человека: Ad1, Ad2, Ad3")
    assert body["answer"] == "В списке 3 человека: " + ALL
    assert len(body["warnings"]) == 1 and "yazılışını dəyişib" in body["warnings"][0]


def test_end_to_end_warns_when_ai_drops_labels(monkeypatch):
    body, _ = run_chat(monkeypatch, RU, "В списке 3 человека: [AD_1] и ещё двое.")
    assert body["answer"] == "В списке 3 человека: Али Алиев и ещё двое."
    assert "[AD_2]" in body["warnings"][0] and "[AD_3]" in body["warnings"][0]


def test_end_to_end_warns_when_no_labels_at_all(monkeypatch):
    body, _ = run_chat(monkeypatch, RU, "В списке три человека.")
    assert "heç biri yoxdur" in body["warnings"][0]


def test_warnings_never_contain_original_values(monkeypatch):
    body, _ = run_chat(monkeypatch, RU, "Ad1 и [AD_2]")
    assert not any(any(part in w for part in ("Алиев", "Мурадов", "Али")) for w in body["warnings"])


def test_masking_is_the_same_in_copy_paste_mode():
    r = client.post("/api/mask", json={"text": RU}).json()
    assert r["masked_text"] == "Сколько человек в списке? Список: [AD_1], [AD_2], [AD_3]"
    assert "Алиев" not in str(r) and "Мурадов" not in str(r)
    assert mask(RU).masked_text == r["masked_text"]


# ---------- Всё строчными: «siyahı: əli əliyev, maqa əliyev, ilham muradov» ----------

LOWER = "siyahıda neçə nəfər? siyahı: əli əliyev, maqa əliyev, ilham muradov"
LOWER_NAMES = ["əli əliyev", "maqa əliyev", "ilham muradov"]


def test_lowercase_list_of_names_is_fully_found():
    assert names(LOWER) == LOWER_NAMES


@pytest.mark.parametrize("text", [
    "mən məmmədov deyirəm",            # «mən» — местоимение, не имя
    "bu əliyevin evidir",              # «əliyevin» не фамилия в чистом виде, «bu» — стоп-слово
    "siyahıda neçə nəfər var?",
    "neçə nəfər gəldi? sonra məmmədov",
])
def test_lowercase_text_without_real_names_is_left_alone(text):
    assert names(text) in ([], ["məmmədov"]) and "mən" not in " ".join(names(text))


def test_lowercase_rule_does_not_change_normal_text():
    """В обычном тексте с заглавными поведение прежнее: одинокое строчное слово перед фамилией не имя."""
    assert names("Bu gün dedi Əliyev gəldi.") == ["Əliyev"]


def test_greeting_before_a_name_is_not_swallowed():
    """«Hörmətli» (слабое окончание -li) не должно приклеиваться к «Toğrul Məmmədov»."""
    assert names("Hörmətli Toğrul Məmmədov, hesabınız bloklandı.") == ["Toğrul Məmmədov"]


def test_weak_surname_still_works_next_to_a_dictionary_name():
    assert names("Bu gün Samir Hüseynli gəldi.") == ["Samir Hüseynli"]


def test_end_to_end_lowercase_request_and_ad1_ad2_ad3_answer(monkeypatch):
    body, received = run_chat(monkeypatch, LOWER, "siyahıda 3 nəfər var, bunlar Ad1, Ad2, Ad3")
    for word in ("əli", "əliyev", "maqa", "ilham", "muradov"):
        assert word not in received[0].replace("siyahı", "").replace("neçə", ""), word
    assert received[0] == "siyahıda neçə nəfər? siyahı: [AD_1], [AD_2], [AD_3]"
    assert body["answer"] == "siyahıda 3 nəfər var, bunlar " + ", ".join(LOWER_NAMES)
    assert len(body["warnings"]) == 1 and "yazılışını dəyişib" in body["warnings"][0]
