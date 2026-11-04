# P0 failure ledger

All attempts below remain on the local private benchmark workspace. The public ledger excludes
raw model output, credentials, private labels and host paths; there were no real model requests.

| Attempt | Result | Resolution / outstanding issue |
| --- | --- | --- |
| AIRA image build 1 | Native extension build failed: missing C++ compiler. | Added `g++` to the controlled image build. |
| AIRA image build 2 / qualification 1 | `pip check` passed, but official help/config/entry failed on undeclared `black`. | Added pinned black and retained failed logs. |
| AIRA qualification 2 | Official help/config/entry failed on undeclared `torch`. | Added pinned Linux ARM CPU torch; requalification is recorded separately below. |
| AIRA qualification 3 | Help omitted a required interpreter; entry lacked `PWD`. | Supplied the official Python interpreter and explicit container working directory. |
| AIRA qualification 4 | Official entry could not resolve source Git SHA from `/work`. | Mounted the pinned checkout read-only and set its Git metadata context. |
| AIRA qualification 5 | Package check, help, and config passed; official zero-step entry failed because the prepared competition dataset was absent. | Dataset access and a full official task remain open; no dataset was fabricated. |
| Agent image build 1 | BuildKit interpreted a literal image SHA as a registry reference. | Changed to an explicit named local-image context. |
| Mac qualification 1 | AIDE zero-step and both data baselines failed because the file-size limit was 16 KiB. | Corrected to 16 MiB; all six subsequent Mac qualification cells passed. |
| Gateway negative test | Invalid capability returned 403; correct capability with zero budget returned 409. | Expected failures; zero upstream requests. |

A failed run is not refilled or deleted. Machine-local records contain command, image, exit
status, time and log hash. The original S0 failure ledger and audit remain unchanged.
