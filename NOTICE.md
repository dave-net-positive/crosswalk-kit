# Third-party content notices

crosswalk-kit's own code is MIT licensed (see `LICENSE`). Some of the data
this repository ships, or that you generate yourself using this repository,
comes from other rights holders under different terms. Those terms are
summarised below.

## NCSC Cyber Assessment Framework (CAF) 4.0

`crosswalk/data/caf_4_0_nodes.json` is derived from the NCSC's Cyber
Assessment Framework, version 4.0.

Contains public sector information licensed under the Open Government
Licence v3.0.

- Licence: [Open Government Licence v3.0](http://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/)
- Source: [NCSC Cyber Assessment Framework](https://www.ncsc.gov.uk/collection/caf)

You may reuse this content, including commercially, provided you comply
with the terms of the OGL v3.0 (in short: acknowledge the source and the
licence, don't imply official endorsement, and don't use it in a misleading
context).

## ISO standards (27001, 27002, 42001, and others)

ISO standards are **not** shipped with this repository. ISO standards are
copyrighted works; ISO and its member bodies sell licensed copies, and
redistributing their text — verbatim or as close paraphrase — without a
licence is an infringement.

If you want to include an ISO framework in your own crosswalk:

1. Obtain your own licensed copy of the standard.
2. Write or adapt an ISO adapter (the adapter *code* in this kit is fine to
   use and share — it contains no ISO text).
3. Run the adapter against your own copy to produce node files locally.
4. **Do not commit, publish, or otherwise share the resulting node files.**
   They contain ISO-derived text and are for your own private or
   internal use only, under your own ISO licence.

## Secure Controls Framework (SCF)

The optional validation workflow in `validation/` checks a crosswalk against
the Secure Controls Framework's control mappings as an independent sanity
check. The SCF itself is not shipped with this repository.

- Licence: CC BY-ND 4.0 (Attribution–NoDerivatives)
- Source: [securecontrolsframework.com](https://securecontrolsframework.com)

Download the SCF spreadsheet yourself from the link above. The
NoDerivatives term means you should not redistribute a modified version of
the SCF itself; any mapping data your own adjudication pipeline derives by
comparing your graph against the SCF should stay local to your own
environment rather than being committed or published.
