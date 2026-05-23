# Volksmond launch, LinkedIn post (early access)

Drafted 2026-05-23. Founder voice (Sean), South African English / Afrikaans. No em or
en dashes anywhere (house rule). Link goes in the FIRST COMMENT, never the post body
(LinkedIn suppresses reach on posts with outbound links).

Recommended posting: one bilingual post, Afrikaans first then English, with a divider
between. It demonstrates the product (Afrikaans + English) and speaks to a SA audience.
Alternative: two separate posts if you want maximum English reach.

The model is confirmed open-source (CC-BY), so the data ask now says so, it strengthens
the ask (donate data, the model is free for everyone). Deliberately left OUT to keep it
clean: the future cloud / read.ai-style VPS product, and any ask for donated COMPUTE.
The compute ask lands far better as a FOLLOW-UP once there is momentum and a model to
point at ("the response was huge, here is the model, now I need compute for v2, who is
in"), so save it. A soft optional future-product line is at the bottom if you want it.

---

## English

I built a private meeting transcription app in three days, because every existing one
either butchered the Afrikaans or sent my confidential conversations off to someone
else's cloud.

Here is the problem a lot of South Africans will recognise. I sit in client meetings I
do not host, often in Afrikaans, or in that real mix of Afrikaans and English we
actually speak. I cannot record them, and I cannot ask for the host's recording
afterwards. So I went looking for a transcription tool, and every option did one of two
things: it turned Afrikaans into gibberish, or it streamed the whole conversation to a
cloud I do not control.

For confidential work, neither is acceptable. So I built my own.

It is called Volksmond, and it runs entirely on your own computer. Your audio and your
transcript never leave the machine. No cloud, no third-party servers, nothing to leak.
It works fully offline.

It handles English extremely well, honestly still a little better than Afrikaans. But
that is the whole point: for an Afrikaans or mixed meeting you finally get a transcript
that is genuinely usable and completely private, where most tools just hand you nonsense.

I am giving it away free in early access. Use it, and tell me what is good and what is
not, so we can make it better together.

One more thing. I want to push the Afrikaans further, and I will put my own GPU to work
on it. If you have a few hours of real Afrikaans, or mixed Afrikaans and English, meeting
audio that is clean and that you have permission to share, send it my way. I will
fine-tune on it and release the improved model open-source, free and attributed, so the
whole Afrikaans community benefits.

Link in the comments. Let me know what you think.

---

## Afrikaans

Ek het in drie dae 'n private vergadering-transkripsieprogram gebou, want elke bestaande
een het óf die Afrikaans verniel, óf my vertroulike gesprekke na iemand anders se wolk
gestuur.

Hier is die probleem wat baie Suid-Afrikaners sal herken. Ek sit in kliëntvergaderings
wat ek nie self aanbied nie, dikwels in Afrikaans, of in daardie egte mengsel van
Afrikaans en Engels wat ons werklik praat. Ek kan dit nie opneem nie, en ek kan nie
agterna die gasheer se opname vra nie. So het ek begin soek na 'n transkripsieprogram, en
elke opsie het een van twee dinge gedoen: dit het Afrikaans in onsin verander, of dit het
die hele gesprek na 'n wolk gestuur wat ek nie beheer nie.

Vir vertroulike werk is nie een aanvaarbaar nie. So het ek my eie gebou.

Dit heet Volksmond, en dit loop heeltemal op jou eie rekenaar. Jou klank en jou
transkripsie verlaat nooit die masjien nie. Geen wolk, geen derdeparty-bedieners, niks om
te lek nie. Dit werk heeltemal vanlyn.

Dit hanteer Engels uitstekend, eerlik nog 'n bietjie beter as Afrikaans. Maar dit is juis
die punt: vir 'n Afrikaanse of gemengde vergadering kry jy uiteindelik 'n transkripsie wat
werklik bruikbaar en heeltemal privaat is, waar die meeste programme jou net onsin gee.

Ek gee dit gratis weg in vroeë toegang. Gebruik dit, en laat weet my wat goed is en wat
nie, sodat ons dit saam beter kan maak.

Nog iets. Ek wil die Afrikaans verder stoot, en ek sal my eie GPU daarvoor inspan. As jy
'n paar uur se regte Afrikaanse, of gemengde Afrikaans en Engelse, vergadering-klank het
wat skoon is en wat jy toestemming het om te deel, stuur dit my kant toe. Ek sal daarop
afrig en die verbeterde model oopbron vrystel, gratis en met erkenning, sodat die hele
Afrikaanse gemeenskap baat.

Skakel in die kommentaar. Laat weet my wat jy dink.

---

## First comment (post this as the first comment, both languages)

EN: Here it is, free in early access: [LINK]. It is Windows for now, fully offline, your
data never leaves your machine. Tell me what works and what does not.

AF: Hier is dit, gratis in vroeë toegang: [LINK]. Dit is voorlopig vir Windows, heeltemal
vanlyn, jou data verlaat nooit jou masjien nie. Laat weet my wat werk en wat nie.

---

## Optional line (only if you want to hint at the future, against my advice)

EN: The free, private version will always stay free and private. Anything I build on top
later is a separate, optional thing.

AF: Die gratis, private weergawe sal altyd gratis en privaat bly. Enigiets wat ek later
daarop bou, is 'n aparte, opsionele ding.

---

## Heads-up on the training-data ask (not for the post)

When people offer recordings: have them confirm in writing that they have the right and
consent to share the audio, and apply the sensitivity rule (the data provider categorises
sensitivity; board, strategic, HR, deal, counselling, and similar are off the table
regardless of consent). A one-line reply asking them to confirm consent is enough for
early days; a proper intake/consent form before any real corpus building. See
`../../SA-ASR-Model/corpus-strategy.md` for the consent clause and archiving workflow.
