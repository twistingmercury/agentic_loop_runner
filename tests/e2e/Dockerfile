FROM python:3.12.15-slim-bookworm AS runner

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# Numeric id, so the suite runs unprivileged like a real user of the tool.
RUN useradd --uid 65532 --create-home --shell /bin/sh tester

WORKDIR /workspace/tests/e2e

# Test dependencies come before the wheel so they stay cached between builds.
COPY tests/e2e/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# build/build.sh passes the exact wheel it just built or found in dist/. Naming
# one file means a stale wheel in dist/ can never be installed. The pins act as
# constraints so the wheel's PyYAML range cannot move the pinned version.
ARG ALR_WHEEL
COPY dist/${ALR_WHEEL} /tmp/${ALR_WHEEL}
RUN pip install --no-cache-dir -c requirements.txt /tmp/${ALR_WHEEL} \
    && rm -f /tmp/${ALR_WHEEL}

COPY --chown=tester:tester tests/e2e/ .

USER 65532:65532

# The cache is useless in a throwaway container and the workdir is not writable.
CMD ["pytest", "-p", "no:cacheprovider"]
