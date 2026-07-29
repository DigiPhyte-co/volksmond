"""Unit tests for the second arm of the cross-channel echo veto (WP-12).

Arm 1 (sys_echo_veto, covered by test_echo_veto.py) needs a CONTINUOUSLY loud far end. Arm 2
(transcribe.fuzzy_echo_veto + SysTextRing) catches the bleed arm 1 correctly refuses: a quiet,
GAPPY far end that the mic still re-hears, where the only extra evidence available is that the
text echoes what the far end just said.

Every MIC/SYS line below is a real adjudicated line from the 2026-07-29 offline validation on the
spprac and ashley meeting wavs (4/4 and 3/3 junk precision, zero adjudicated real-speech loss).
The KEEP cases are the safety contract: "Yeah, it's a pleasure." must survive a silent mic under a
loud far end forever, because the content-token floor - not the energy - is what protects it.

No model load, no audio: hand-built energy rings + the real token helpers.

Run:  python tests/test_xchan_echo.py   (exit 0 = pass)
"""
import inspect
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from live_transcribe import transcribe as T


# --- fixtures ---------------------------------------------------------------------------------
# The adjudicated junk (MIC fabrication) and its far-end original.
GHOST_1 = "The area of maintenance is key with the SAE and the functionality."
ORIG_1 = "The overall governance is key, where I think in SAP and the functionality"
GHOST_2 = "the financial controls if they run in SAP"
ORIG_2 = "But the finance controls, the financial controls, if they run in SAP"
# The adjudicated real speech that must survive.
REAL_SHORT = "Yeah, it's a pleasure."
REAL_SHORT_FAR = "It is a real pleasure to work with the team, thank you."
REAL_PARTIAL = "There should be no hesitation, et cetera."
REAL_PARTIAL_FAR = "So we can move ahead with that, etcetera, on the next point."
# Sequential dialogue: a real, quiet reply to a far-end line that had already FINISHED. It shares
# most of its words with that line by nature, which is what a proximity-only rule mistook for echo.
REPLY_MIC = "I will send the updated budget tomorrow"
REPLY_FAR = "Please send the updated budget tomorrow"

MIC_T0, MIC_T1 = 30.0, 33.0      # the MIC segment span used throughout


def ring(db, t0, t1, step=0.1):
    """A constant-level energy ring over [t0, t1] at the real 100 ms frame cadence."""
    r = T.SysEnergyRing(retain_s=1e6)
    n = int(round((t1 - t0) / step))
    for i in range(n + 1):
        r.add(t0 + i * step, db)
    return r


def sys_text(*items):
    """A SysTextRing pre-loaded with (t_start, t_end, text) far-end segments.

    Spans, not just arrival times: the veto only counts a far-end line that was SOUNDING while the
    mic segment ran, so every fixture here has to say when its far end stopped talking."""
    ring_ = T.SysTextRing()
    for t0, t1, text in items:
        ring_.add(t0, text, t1)
    return ring_


def veto(text, own_db=-29.0, far_db=-18.0, far_text=None, t0=MIC_T0, t1=MIC_T1,
         mic_ring=True, sys_ring=True, text_ring=True, far_t=27.0, far_end=None):
    """Run arm 2 over one MIC segment. Defaults are the measured bleed case: silent mic, far end
    11 dB louder, and the far-end original STILL SOUNDING across the mic segment (it started a few
    seconds earlier and runs half a second past the end), which is what bleed physically is."""
    mr = ring(own_db, t0 - 0.5, t1 + 0.5) if mic_ring else None
    sr = ring(far_db, t0 - 0.5, t1 + 0.5) if sys_ring else None
    fe = (t1 + 0.5) if far_end is None else far_end
    tr = (sys_text((far_t, fe, far_text)) if (text_ring and far_text)
          else (T.SysTextRing() if text_ring else None))
    return T.fuzzy_echo_veto(text, t0, t1, mr, sr, tr)


def overlap_of(mic, far):
    """The veto's own overlap score for a MIC/SYS pair (MIC content tokens as denominator)."""
    mw = T._content_words(mic)
    return T.dedup._shared_count(mw, T._content_words(far)) / len(mw)


# --- the adjudicated junk: dropped ------------------------------------------------------------
def test_governance_ghost_dropped():
    drop, own, marg, ov, why = veto(GHOST_1, far_text=ORIG_1)
    assert drop, f"the maintenance/SAE ghost must be dropped: {why}"
    assert (own, marg) == (-29.0, 11.0) and ov >= T._XCHAN2_MIN_OVERLAP, (own, marg, ov)


def test_sap_controls_ghost_dropped():
    drop, own, marg, ov, why = veto(GHOST_2, own_db=-31.0, far_text=ORIG_2)
    assert drop, f"the SAP-controls ghost must be dropped: {why}"
    assert marg == 13.0 and ov == 1.0, (marg, ov)   # every content word came from the far end


def test_measured_overlaps_are_the_scored_ones():
    """Pins what the rule actually scores, so a tokeniser change shows up here and not in the
    field. The 0.333 is only TWO shared words ("key", "functionality") out of six: the thinnest
    real drop in the validation set, and the reason the 0.30 threshold is where it is."""
    assert round(overlap_of(GHOST_1, ORIG_1), 3) == 0.333
    assert overlap_of(GHOST_2, ORIG_2) == 1.0
    assert round(overlap_of(REAL_PARTIAL, REAL_PARTIAL_FAR), 3) == 0.167


# --- the adjudicated real speech: kept --------------------------------------------------------
def test_short_real_reply_kept_however_quiet():
    """THE decisive protection case. Silent mic (-29), far end 11 dB up, and the far end even
    said "pleasure" - but two content words is under the floor, so the text is never judged."""
    drop, own, marg, ov, why = veto(REAL_SHORT, far_text=REAL_SHORT_FAR)
    assert not drop and why == "tok=2", why
    assert (own, marg, ov) == (-29.0, 11.0, None), (own, marg, ov)
    assert overlap_of(REAL_SHORT, REAL_SHORT_FAR) >= 0.5, "and it would have scored high enough"


def test_partial_overlap_kept():
    # shares only an "et cetera" fragment with the far end: 0.167 < 0.30 -> real speech
    drop, own, marg, ov, why = veto(REAL_PARTIAL, far_text=REAL_PARTIAL_FAR)
    assert not drop and why == "ov=0.17", why
    assert ov < T._XCHAN2_MIN_OVERLAP


def test_loud_mic_kept_regardless_of_text():
    # a mic above the ceiling really spoke; text overlap is then none of our business
    drop, own, marg, ov, why = veto(GHOST_2, own_db=-20.0, far_text=ORIG_2)
    assert not drop and why == "loud", why
    assert marg is None and ov is None, "the text must not even be scored once the mic is loud"


def test_small_margin_kept():
    # far end only 7 dB up: not the asymmetry bleed shows, so keep even on a perfect text match
    drop, own, marg, ov, why = veto(GHOST_2, own_db=-29.0, far_db=-22.0, far_text=ORIG_2)
    assert not drop and why == "marg=7.0", why


def test_sub_half_second_kept():
    drop, own, marg, ov, why = veto(GHOST_2, t0=30.0, t1=30.4, far_text=ORIG_2)
    assert not drop and why == "short", why


# --- fail-safe: no evidence, no drop ----------------------------------------------------------
def test_no_mic_ring_kept():
    drop, _, _, _, why = veto(GHOST_2, mic_ring=False, far_text=ORIG_2)
    assert not drop and why == "nomicring", why


def test_empty_mic_window_kept():
    # the ring exists but holds nothing for this segment (backlog past its retention)
    drop, _, _, _, why = T.fuzzy_echo_veto(GHOST_2, MIC_T0, MIC_T1, ring(-29.0, 0.0, 5.0),
                                           ring(-18.0, 29.5, 33.5), sys_text((27.0, 33.5, ORIG_2)))
    assert not drop and why == "nomic", why


def test_no_sys_ring_kept():
    drop, _, _, _, why = veto(GHOST_2, sys_ring=False, far_text=ORIG_2)
    assert not drop and why == "nosysring", why


def test_empty_sys_window_kept():
    drop, _, _, _, why = T.fuzzy_echo_veto(GHOST_2, MIC_T0, MIC_T1, ring(-29.0, 29.5, 33.5),
                                           ring(-18.0, 0.0, 5.0), sys_text((27.0, 33.5, ORIG_2)))
    assert not drop and why == "nosys", why


def test_no_sys_text_at_all_kept():
    drop, _, _, ov, why = veto(GHOST_2, far_text=None)
    assert not drop and why == "ov=0.00", why
    assert ov == 0.0


def test_no_text_ring_kept():
    drop, _, _, _, why = veto(GHOST_2, text_ring=False, far_text=ORIG_2)
    assert not drop and why == "nosystext", why


def test_far_end_text_outside_the_window_kept():
    # the same far-end original, published 8 s before the segment starts: outside +/-6 s
    drop, _, _, ov, why = veto(GHOST_2, far_text=ORIG_2, far_t=MIC_T0 - 8.0)
    assert not drop and ov == 0.0, why
    # and 6.0 s before is still inside (inclusive window)
    drop, _, _, ov, why = veto(GHOST_2, far_text=ORIG_2, far_t=MIC_T0 - 6.0)
    assert drop and ov == 1.0, why


# --- simultaneity: bleed is concurrent audio, dialogue is not ---------------------------------
def test_sequential_reply_kept_when_the_far_end_had_finished():
    """THE regression this rule exists for. A real, quiet reply to a far-end request repeats most
    of its words ("Please send the updated budget tomorrow" -> "I will send the updated budget
    tomorrow"), so the text evidence is damning and the energy asymmetry is exactly bleed's. The
    only thing that separates it from bleed is time: the far end had STOPPED 1.5 s before this
    speaker started. A proximity-only rule dropped a real sentence here."""
    drop, own, marg, ov, why = veto(REPLY_MIC, far_text=REPLY_FAR,
                                    far_t=24.0, far_end=MIC_T0 - 1.5)
    assert not drop, f"a reply to a FINISHED far-end line must never be dropped: {why}"
    assert (own, marg, ov) == (-29.0, 11.0, 0.0), (own, marg, ov)
    # and it would have scored well over the threshold had the spans overlapped
    assert overlap_of(REPLY_MIC, REPLY_FAR) >= 0.8
    assert veto(REPLY_MIC, far_text=REPLY_FAR, far_t=24.0, far_end=31.0)[0] is True, \
        "the same words WHILE the far end was still talking are bleed and must drop"


def test_overlapping_span_echo_dropped():
    # The far end starts before the mic segment and is still going a second into it: the mic can
    # only be re-hearing it. Dropped on the same text evidence the sequential case is spared.
    drop, _, _, ov, why = veto(GHOST_2, far_text=ORIG_2, far_t=27.0, far_end=31.0)
    assert drop and ov == 1.0, why
    # a far end that starts DURING the mic segment and runs past it: also concurrent, also dropped
    assert veto(GHOST_2, far_text=ORIG_2, far_t=31.0, far_end=36.0)[0] is True


def test_simultaneity_pad_is_the_boundary():
    """The 1.0 s pad absorbs segmentation slop and nothing more. A far end that stops exactly one
    second before the mic segment starts still counts (boundaries touch); a tenth of a second
    earlier does not."""
    pad = T._XCHAN2_SIMUL_PAD
    assert veto(GHOST_2, far_text=ORIG_2, far_t=24.0, far_end=MIC_T0 - pad)[0] is True
    assert veto(GHOST_2, far_text=ORIG_2, far_t=24.0, far_end=MIC_T0 - pad - 0.1)[0] is False
    # symmetrical at the other end: a far end that starts just after the mic segment stops
    assert veto(GHOST_2, far_text=ORIG_2, far_t=MIC_T1 + pad, far_end=38.0)[0] is True
    assert veto(GHOST_2, far_text=ORIG_2, far_t=MIC_T1 + pad + 0.1, far_end=38.0)[0] is False


def test_the_six_second_window_is_only_the_scan_bound():
    """+/- 6 s survives as the ring-scan bound, so the two rules are visibly independent: inside
    the window but not overlapping is a KEEP, and overlapping is checked within it."""
    src = inspect.getsource(T.fuzzy_echo_veto)
    assert "sys_text.near(abs_start - window_s, abs_end + window_s," in src
    assert "span=(abs_start - simul_pad, abs_end + simul_pad)" in src
    # 5 s before the segment: well inside +/- 6 s, and still kept because the spans do not touch
    assert veto(GHOST_2, far_text=ORIG_2, far_t=MIC_T0 - 5.0, far_end=MIC_T0 - 2.0)[0] is False


def test_ordering_race_fails_safe():
    """ACCEPTED recall limitation, pinned so it stays a limitation and never becomes a wrong drop:
    a MIC segment can be judged before the concurrent SYS text has been published into the ring
    (see the note at the arm-2 call site). With the reference absent the arm scores nothing and
    KEEPS the line. Deferring the judgement to the MIC publish-delay flush is the known future
    improvement; guessing without a reference is not."""
    e = _RouteSeam()
    mr, sr = ring(-29.0, 29.5, 33.5), ring(-18.0, 29.5, 33.5)
    # the SYS original has NOT reached _route yet (the transcription worker is mid-race)
    drop, _, _, ov, why = T.fuzzy_echo_veto(GHOST_2, MIC_T0, MIC_T1, mr, sr, e._sys_text)
    assert not drop and ov == 0.0, f"a missing reference must fail safe (keep): {why}"
    # once the same SYS segment IS published, the identical MIC line drops: only the ordering
    # changed the verdict, which is what makes this a recall limit rather than a rule
    e._route(T.Segment("SYS", 27.0, 33.5, ORIG_2))
    assert T.fuzzy_echo_veto(GHOST_2, MIC_T0, MIC_T1, mr, sr, e._sys_text)[0] is True


# --- thresholds pinned ------------------------------------------------------------------------
def test_threshold_constants():
    assert (T._XCHAN2_MIN_DUR, T._XCHAN2_MIC_CEILING, T._XCHAN2_MARGIN_DB) == (0.5, -27.0, 10.0)
    assert (T._XCHAN2_MIN_TOKENS, T._XCHAN2_MIN_OVERLAP, T._XCHAN2_TEXT_WINDOW) == (4, 0.30, 6.0)
    assert T._XCHAN2_SIMUL_PAD == 1.0


def test_ceiling_and_margin_are_inclusive_boundaries():
    # own exactly at the ceiling -> kept (must be strictly below); one tenth under -> judged
    assert veto(GHOST_2, own_db=-27.0, far_db=-17.0, far_text=ORIG_2)[4] == "loud"
    assert veto(GHOST_2, own_db=-27.1, far_db=-17.1, far_text=ORIG_2)[0] is True
    # margin exactly 10 dB -> drops (>= margin_db)
    assert veto(GHOST_2, own_db=-28.0, far_db=-18.0, far_text=ORIG_2)[0] is True


def test_four_content_words_is_the_floor():
    # three content words are never judged, four are (with the same far-end text either way)
    far = "the budget report is on the shared drive already"
    assert veto("budget report shared", far_text=far)[4] == "tok=3"
    assert veto("budget report shared drive", far_text=far)[0] is True


def test_plain_p90_not_the_active_subset():
    """Condition 3 is p90 of ALL far-end frames, deliberately not arm 1's p70-of-active. A far
    end that is loud a third of the time and silent the rest is the case this arm exists for:
    arm 1 refuses it on coverage, and this p90 still sees the loud third."""
    gappy = T.SysEnergyRing(retain_s=1e6)
    for i in range(31):                      # 3.0 s: loud every other frame, room floor between
        gappy.add(29.5 + i * 0.1, -18.0 if i % 2 else -60.0)
    drop, own, marg, ov, why = T.fuzzy_echo_veto(GHOST_2, MIC_T0, MIC_T1,
                                                 ring(-31.0, 29.5, 33.5), gappy,
                                                 sys_text((27.0, 33.5, ORIG_2)))
    assert drop, f"a gappy but loud far end must still arm the veto: {why}"
    assert marg == 13.0, marg
    # the same ring under arm 1: coverage is below its 0.60 floor, so arm 1 correctly refuses
    a1_drop, a1_why = T.sys_echo_veto(None, gappy, MIC_T0, MIC_T1, 8,
                                      mic_ring=ring(-31.0, 29.5, 33.5))
    assert not a1_drop and a1_why.startswith("cov="), a1_why


# --- the fuzzy matcher is dedup's, and it is load-bearing -------------------------------------
def test_fuzzy_matcher_is_dedups():
    """Pins the matcher's ACTUAL behaviour at dedup's 0.78 cutoff rather than an assumed one.
    A near-spelling counts as the same word; two words that merely start alike do not - notably
    "sae"/"sap" (ratio 0.667) does NOT pair, so GHOST_1 drops on its exact words alone."""
    assert T.dedup.FUZZY_RATIO == 0.78
    assert T.dedup._shared_count(["dinsdag"], ["dansdag"]) == 1     # ratio 0.857
    assert T.dedup._shared_count(["begroting"], ["begrotings"]) == 1
    assert T.dedup._shared_count(["sae"], ["sap"]) == 0             # ratio 0.667 < 0.78
    assert T.dedup._shared_count(["pensel"], ["pencil"]) == 0       # ratio 0.667 < 0.78
    # one-to-one: two MIC copies of a word cannot both pair with one SYS word
    assert T.dedup._shared_count(["controls", "controls"], ["controls"]) == 1


def test_fuzziness_changes_the_verdict():
    """A garbled echo whose words are all MIS-HEARD: exact-word overlap is 0.20 and would keep
    it, fuzzy overlap is 0.60 and drops it. This is why the matcher is dedup's and not a set
    intersection."""
    mic = "Dinsdag se begroting verslag is afgehandel"
    far = "Dansdag se begrotings dokument is klaar"
    mw, fw = T._content_words(mic), T._content_words(far)
    exact = len(set(mw) & set(fw)) / len(mw)
    assert exact < T._XCHAN2_MIN_OVERLAP, exact
    assert overlap_of(mic, far) >= 0.6
    assert veto(mic, far_text=far)[0] is True


def test_two_shared_words_in_a_six_word_line_drops():
    """The documented sensitivity, pinned so nobody rediscovers it in the field: at a 6-content-
    word MIC line, 0.30 means "two shared words". REAL_PARTIAL survives only because the far end
    said "etcetera" as one word; had it said "et cetera", the same line would score 0.333 and be
    dropped. If adjudication ever rejects that, the lever is min_overlap (0.34+) or a shared-word
    floor, not the energy arms."""
    variant = "So we can move ahead with that, et cetera, on the next point."
    assert round(overlap_of(REAL_PARTIAL, variant), 3) == 0.333
    assert veto(REAL_PARTIAL, far_text=variant)[0] is True


# --- SysTextRing ------------------------------------------------------------------------------
def test_text_ring_window_is_inclusive_and_bounded():
    r = sys_text((10.0, 14.0, ORIG_1), (20.0, 24.0, ORIG_2))
    assert len(r.near(10.0, 20.0)) == 2
    assert r.near(10.1, 19.9) == []
    assert r.near(0.0, 5.0) == []


def test_text_ring_retention_and_maxlen():
    r = T.SysTextRing()                                  # maxlen 8, retain 20 s
    for i in range(12):
        r.add(i * 5.0, f"item {i} of the far end talking")
    assert r.near(0.0, 1e6) and len(r.near(0.0, 1e6)) <= 8
    assert r.near(0.0, 30.0) == [], "frames older than the retention window must be evicted"
    r2 = T.SysTextRing(maxlen=3, retain_s=1e6)
    for i in range(6):
        r2.add(float(i), f"line {i} about the budget")
    assert len(r2.near(0.0, 1e6)) == 3, "maxlen must bound the ring independently of time"


def test_text_ring_span_filter():
    """near(span=...) is the simultaneity rule: START inside the scan bound AND the segment's own
    span intersecting `span`. Checked at both edges and for the containment cases."""
    r = sys_text((10.0, 14.0, ORIG_1))
    scan = (0.0, 1e6)
    assert r.near(*scan, span=(14.0, 20.0)) and r.near(*scan, span=(0.0, 10.0)), \
        "touching boundaries must count as overlap"
    assert r.near(*scan, span=(14.01, 20.0)) == [], "a span that starts after the SYS line ended"
    assert r.near(*scan, span=(0.0, 9.99)) == [], "a span that ends before the SYS line began"
    assert r.near(*scan, span=(11.0, 12.0)), "a span wholly inside the SYS line still overlaps"
    assert r.near(20.0, 30.0, span=(10.0, 14.0)) == [], "the scan bound still applies"


def test_text_ring_defaults_to_a_zero_length_span():
    # A caller that does not know the end contributes no echo evidence rather than a guessed one.
    r = T.SysTextRing()
    r.add(10.0, ORIG_1)
    assert r.near(0.0, 1e6) and r.near(0.0, 1e6, span=(10.0, 10.0)), "the start still counts"
    assert r.near(0.0, 1e6, span=(10.1, 20.0)) == [], "no end means no span to overlap with"


def test_text_ring_ignores_contentless_lines():
    r = T.SysTextRing()
    r.add(10.0, "...")
    r.add(11.0, "and the, yeah")      # all fillers
    assert r.near(0.0, 1e6) == []


def test_text_ring_clear():
    r = sys_text((10.0, 14.0, ORIG_1))
    r.clear()
    assert r.near(0.0, 1e6) == []


# --- Engine wiring (no model load) ------------------------------------------------------------
class _RouteSeam:
    """Engine._route without loading a model: the single point a SYS segment reaches the
    transcript, and therefore where the echo reference is recorded."""
    _route = T.Engine._route
    _fanout = T.Engine._fanout

    def __init__(self, veto2=True):
        self.subscribers = []
        self._pending_mic = []
        self._xchan_veto2 = veto2
        self._sys_text = T.SysTextRing()


def test_published_sys_becomes_the_echo_reference():
    e = _RouteSeam()
    seen = []
    e.subscribers.append(seen.append)
    e._route(T.Segment("SYS", 27.0, 33.0, ORIG_2))
    e._route(T.Segment("MIC", 30.0, 33.0, GHOST_2))
    assert len(seen) == 1 and seen[0].source == "SYS", "SYS publishes at once, MIC is held"
    assert e._sys_text.near(20.0, 40.0) == [T._content_words(ORIG_2)]
    # BOTH ends of the published segment are recorded, or the simultaneity test has nothing to
    # work with: the reference here really does span 27.0-33.0.
    assert e._sys_text.near(20.0, 40.0, span=(32.9, 40.0)), "the SYS end time was not recorded"
    assert e._sys_text.near(20.0, 40.0, span=(33.1, 40.0)) == [], "the end time is wrong"
    assert len(e._pending_mic) == 1, "the MIC hold must be untouched"


def test_kill_switch_leaves_the_ring_empty():
    e = _RouteSeam(veto2=False)
    e._route(T.Segment("SYS", 27.0, 33.0, ORIG_2))
    assert e._sys_text.near(0.0, 1e6) == [], "SA_LIVE_XCHAN_VETO2=0 must be fully inert"


def test_kill_switch_read_once_in_init():
    src = inspect.getsource(T.Engine.__init__)
    assert 'os.environ.get("SA_LIVE_XCHAN_VETO2", "1") != "0"' in src
    assert src.count("SA_LIVE_XCHAN_VETO2") == 1, "read once, like the other guards"
    # arm 1 keeps its own switch
    assert 'os.environ.get("SA_LIVE_XCHAN_VETO", "1") != "0"' in src


def test_run_order_and_gating():
    """Arm 2 sits after arm 1 (the cheaper absolute test) and before the loop guard, so a vetoed
    line never seeds the loop history, and it is MIC-only and switchable."""
    src = inspect.getsource(T.Engine._run)
    i_arm1 = src.index("sys_echo_veto(")
    i_arm2 = src.index("fuzzy_echo_veto(")
    i_loop = src.index("self._recent.observe(")
    i_route = src.index("self._route(out)")
    assert i_arm1 < i_arm2 < i_loop < i_route, (i_arm1, i_arm2, i_loop, i_route)
    assert 'if source == "MIC" and self._xchan_veto2:' in src
    assert "self._ring_for(\"MIC\"), self.sys_env, self._sys_text" in src
    assert "xchan-echo dropped MIC" in src


def test_reference_is_never_recent_emissions():
    """RecentEmissions is cleared on a model change AND a device switch, only fills with the loop
    guard on, and holds SUPPRESSED text. Using it would silently disarm this veto, so the ring is
    cleared on engine start and nowhere else."""
    assert "self._sys_text.clear()" in inspect.getsource(T.Engine.start)
    for meth in (T.Engine._apply_pending_change, T.Engine._apply_pending_recent_reset,
                 T.Engine.request_loop_history_reset):
        assert "_sys_text" not in inspect.getsource(meth), meth.__name__
    assert "_recent" not in inspect.getsource(T.fuzzy_echo_veto)


def test_ring_is_locked():
    r = T.SysTextRing()
    assert isinstance(r._lock, type(threading.Lock()))


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print("ok ", t.__name__)
    print(f"\nall {len(tests)} cross-channel fuzzy echo tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
