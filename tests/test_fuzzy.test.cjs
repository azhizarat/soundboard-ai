const test = require("node:test");
const assert = require("node:assert/strict");
const F = require("../ui/fuzzy.js");

test("normaliza acentos e caixa", () => {
  assert.equal(F.normalizeText("  Não   VS  Sim "), "nao vs sim");
});

test("igualdade vence prefixo", () => {
  assert.ok(F.score("nao", "não") > F.score("nao", "não vs sim"));
});

test("prefixo vence substring no meio", () => {
  assert.ok(F.score("nao", "não vs sim") > F.score("nao", "sim não"));
});

test("subsequência encontra iniciais", () => {
  assert.ok(F.score("nvs", "não vs sim") !== null);
});

test("sem correspondência retorna null", () => {
  assert.equal(F.score("xyz", "não vs sim"), null);
});

test("scoreClip pondera título acima de transcrição", () => {
  const speakers = { s1: { name: "Lucas" } };
  const titleHit = F.scoreClip({ title: "grito", transcript: "" }, "grito", speakers);
  const transcriptHit = F.scoreClip({ title: "outro", transcript: "grito" }, "grito", speakers);
  assert.ok(titleHit > transcriptHit);
});

test("scoreClip encontra nome do interlocutor", () => {
  const speakers = { s1: { name: "Lucas Barreiros" } };
  assert.ok(F.scoreClip({ title: "x", speaker_ids: ["s1"] }, "lucas", speakers) !== null);
});

test("highlight escapa HTML e marca o trecho", () => {
  const out = F.highlight("<b>Não</b> vs sim", "nao");
  assert.ok(!out.includes("<b>"));
  assert.ok(out.includes("<mark>Não</mark>"));
});

test("highlight sem query devolve texto escapado", () => {
  assert.equal(F.highlight("<img src=x>", ""), "&lt;img src=x&gt;");
});

test("highlight normaliza espaços em branco como score()", () => {
  const text = "a\nnao vs";
  assert.ok(F.score("a nao", text) !== null);
  assert.equal(F.highlight(text, "a nao"), "<mark>a\nnao</mark> vs");
});

test("highlight mantém acentos combinantes dentro do mark", () => {
  const text = "cafe\u0301";
  assert.equal(F.highlight(text, "cafe"), "<mark>cafe\u0301</mark>");
});
