import json

try:
    await page.set_viewport_size({"width": 1920, "height": 1080})
    await page.wait_for_load_state("networkidle")

    await page.locator('[data-testid="login-email"]').fill('test@aurem.dev')
    await page.locator('[data-testid="login-password"]').fill('AuremTest2026!')
    await page.locator('[data-testid="login-submit"]').click()
    await page.wait_for_url('**/dashboard**', timeout=20000)
    await page.wait_for_timeout(1000)

    start_hits = {"count": 0}
    stream_hits = {"count": 0}

    async def handle_loop_start(route):
        start_hits["count"] += 1
        loop_id = f"qa-verify-retry-{start_hits['count']}"
        await route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"loop_id": loop_id, "async_start": True, "plan": None}),
        )

    def sse_frame(loop_id, seq, event):
        return f"id: {loop_id}:{seq}\ndata: {json.dumps(event)}\n\n"

    async def handle_loop_stream(route):
        stream_hits["count"] += 1
        hit = stream_hits["count"]
        if hit == 1:
            # Hold the first synthetic stream briefly so the test can
            # observe the fresh-kickoff reset state before verify failures arrive.
            await page.wait_for_timeout(1200)
            loop_id = "qa-verify-retry-1"
            events = [
                {
                    "loop_id": loop_id,
                    "state": "verifying",
                    "phase": "verify",
                    "message": "Verify failed after 2 attempts",
                    "data": {"type": "narration", "narration_step": "verify", "tone": "danger", "text": "Verify failed after 2 attempts"},
                },
                {
                    "loop_id": loop_id,
                    "state": "verifying",
                    "phase": "verify",
                    "message": "Verify failed after 2 attempts",
                    "data": {"type": "narration", "narration_step": "verify", "tone": "danger", "text": "Verify failed after 2 attempts"},
                },
                {
                    "loop_id": loop_id,
                    "state": "failed",
                    "phase": "verify",
                    "message": "Loop failed after verify retry cap",
                    "data": {"type": "narration", "narration_step": "verify", "tone": "danger", "text": "Loop failed after verify retry cap"},
                },
            ]
            body = "".join(sse_frame(loop_id, i + 1, ev) for i, ev in enumerate(events))
        else:
            body = ""
        await route.fulfill(status=200, headers={"content-type": "text/event-stream"}, body=body)

    await page.route('**/api/aurem-dev/loop/start', handle_loop_start)
    await page.route('**/api/aurem-dev/loop/*/stream', handle_loop_stream)

    if await page.locator('[data-testid="cookie-accept-btn"]').count() > 0:
        await page.locator('[data-testid="cookie-accept-btn"]').click(force=True)

    loop_toggle = page.locator('[data-testid="loop-mode-toggle"]')
    if 'OFF' in (await loop_toggle.inner_text()):
        await loop_toggle.click()
    assert 'ON' in (await loop_toggle.inner_text()), 'Loop mode did not toggle ON'

    await page.locator('[data-testid="chat-input"]').fill('QA verify retry counter first loop')
    await page.locator('[data-testid="chat-send"]').click()

    # Fresh kickoff starts with verifyRetryCount=0, so the retry pill should be hidden.
    await page.wait_for_selector('[data-testid="loop-step-bar"]', timeout=10000)
    assert await page.locator('[data-testid="loop-retry-pill"]').count() == 0, 'Retry pill was visible before any verify failure'

    retry_text = None
    for _ in range(30):
        await page.wait_for_timeout(250)
        if await page.locator('[data-testid="loop-retry-pill"]').count() > 0:
            retry_text = (await page.locator('[data-testid="loop-retry-pill"]').inner_text()).strip()
            if retry_text == '2/3 retries':
                break
    assert retry_text == '2/3 retries', f'Retry pill did not live-sync to 2/3 retries; saw {retry_text!r}'
    assert retry_text != 'heal 1/2', 'Retry pill is still stuck on stale inner self-heal count'

    await page.locator('[data-testid="chat-input"]').fill('QA verify retry counter second fresh loop')
    await page.locator('[data-testid="chat-send"]').click()
    for _ in range(20):
        await page.wait_for_timeout(100)
        if start_hits["count"] >= 2:
            break
    await page.wait_for_timeout(400)
    assert await page.locator('[data-testid="loop-retry-pill"]').count() == 0, 'Retry pill did not reset hidden on fresh loop kickoff'

    error_text = await page.evaluate("""() => {
    const errorElements = Array.from(document.querySelectorAll('.error, [class*="error"], [id*="error"]'));
    return errorElements.map(el => el.textContent).join(", ");
    }""")
    if error_text:
        print(f"Found error message: {error_text}")
    else:
        print("No error messages found on the page")
    print('PASS verify retry counter live-sync synthetic SSE UI test')
except Exception as e:
    print('FAIL verify retry counter live-sync synthetic SSE UI test:', repr(e))
    await page.screenshot(path='/app/test_reports/bug_verification_artifacts/verify_retry_counter_probe/failure.jpg', quality=40, full_page=False)
    raise