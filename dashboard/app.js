const GO2RTC_BASE_URL = window.location.origin;
const DETECTOR_BASE_URL = `${window.location.protocol}//${window.location.hostname}:8000`;
const peerConnections = {};

// Camera list and metadata loaded from /api/cameras on startup.
let cameras = [];
const cameraMeta = {};

const state = {
    activeFilter: 'all',
    selectedEventId: null,
    selectedEvent: null,
    eventsCache: [],
    health: null,
    storage: null,
    errors: {
        events: null,
        health: null,
        storage: null,
    },
    lastEventsRefreshAt: null,
    lastHealthRefreshAt: null,
    lastStorageRefreshAt: null,
    live: {},
    playback: {
        hls: null,
        clipUrl: '',
        error: null,
    },
};

document.addEventListener('DOMContentLoaded', async () => {
    initializeControls();
    initializePlayback();
    await loadCameras();
    initializeLiveView();
    refreshAll();
    setInterval(loadEvents, 15000);
    setInterval(loadHealth, 15000);
    setInterval(loadStorage, 30000);
});

async function loadCameras() {
    const grid = document.getElementById('cameras-grid');
    const emptyMessage = document.getElementById('cameras-grid-empty');

    try {
        const response = await fetch(`${DETECTOR_BASE_URL}/api/cameras`);
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }
        const payload = await response.json();
        const items = payload.items || [];

        cameras = items.map((item) => item.name);
        items.forEach((item) => {
            cameraMeta[item.name] = item;
            state.live[item.name] = { webrtc: 'idle' };
        });

        if (emptyMessage) {
            emptyMessage.remove();
        }
        renderCameraTiles(grid, items);

        const summary = document.getElementById('live-online-count');
        const recorderSummary = document.getElementById('recorder-online-count');
        if (summary) summary.textContent = `0 / ${items.length}`;
        if (recorderSummary) recorderSummary.textContent = `0 / ${items.length}`;
    } catch (error) {
        console.error('Failed to load cameras:', error);
        if (emptyMessage) {
            emptyMessage.textContent = `Could not load cameras from /api/cameras (${error.message}).`;
        }
    }
}

function renderCameraTiles(grid, items) {
    const template = document.getElementById('camera-card-template');
    if (!template || !grid) {
        return;
    }

    items.forEach((item) => {
        const fragment = template.content.cloneNode(true);
        const article = fragment.querySelector('article');
        article.id = `camera-${item.name}`;
        article.dataset.cameraName = item.name;

        const kicker = fragment.querySelector('[data-role="kicker"]');
        if (kicker) kicker.textContent = `Live feed · ${item.name}`;

        const displayName = fragment.querySelector('[data-role="display-name"]');
        if (displayName) displayName.textContent = item.display_name || item.name;

        const pill = fragment.querySelector('[data-role="pill"]');
        if (pill) pill.id = `camera-pill-${item.name}`;

        const videoShell = fragment.querySelector('[data-role="video-shell"]');
        if (videoShell) videoShell.id = `video-shell-${item.name}`;

        const videoContainer = fragment.querySelector('[data-role="video-container"]');
        if (videoContainer) videoContainer.dataset.camera = item.name;

        const video = fragment.querySelector('[data-role="video"]');
        if (video) video.id = `video-${item.name}`;

        const overlay = fragment.querySelector('[data-role="overlay"]');
        if (overlay) overlay.id = `overlay-${item.name}`;

        const map = {
            'live-state': `camera-${item.name}-live`,
            'stream-state': `camera-${item.name}-stream`,
            'recorder-state': `camera-${item.name}-recorder`,
            'alert-state': `camera-${item.name}-alert`,
            'support': `camera-${item.name}-support`,
        };
        Object.entries(map).forEach(([role, id]) => {
            const node = fragment.querySelector(`[data-role="${role}"]`);
            if (node) node.id = id;
        });

        fragment.querySelector('[data-action="reconnect"]').addEventListener('click', () => connectCamera(item.name));
        fragment.querySelector('[data-action="fullscreen"]').addEventListener('click', () => toggleFullscreen(`video-${item.name}`));
        fragment.querySelector('[data-action="snapshot"]').addEventListener('click', () => captureSnapshot(item.name));

        grid.appendChild(fragment);
    });
}

function initializeLiveView() {
    cameras.forEach((camera) => {
        const container = document.querySelector(`#video-shell-${camera} .video-container`);
        const video = document.getElementById(`video-${camera}`);
        if (!container || !video) return;
        container.addEventListener('click', () => connectCamera(camera));
        video.addEventListener('loadedmetadata', () => applyLiveCropPresentation(camera));
    });

    setTimeout(() => {
        cameras.forEach(connectCamera);
    }, 500);
}

function initializeControls() {
    document.querySelectorAll('.filter-button').forEach((button) => {
        button.addEventListener('click', () => {
            state.activeFilter = button.dataset.filter;
            document.querySelectorAll('.filter-button').forEach((candidate) => {
                const active = candidate === button;
                candidate.classList.toggle('active', active);
                candidate.setAttribute('aria-pressed', active ? 'true' : 'false');
            });
            reconcileSelection();
            renderEvents();
        });
    });

    document.getElementById('refresh-dashboard').addEventListener('click', refreshAll);

    document.getElementById('events-list').addEventListener('click', (event) => {
        const row = event.target.closest('[data-event-id]');
        if (!row) {
            return;
        }

        state.selectedEventId = Number(row.dataset.eventId);
        renderEvents();
    });

    document.getElementById('detail-retry-playback').addEventListener('click', () => {
        if (state.selectedEvent) {
            startDetailPlayback(state.selectedEvent);
        }
    });
}

function initializePlayback() {
    const video = document.getElementById('detail-video');
    video.addEventListener('loadedmetadata', () => {
        hidePlaybackStatus();
        applyDetailPresentation(state.selectedEvent);
    });
    video.addEventListener('canplay', hidePlaybackStatus);
    video.addEventListener('error', () => {
        showPlaybackStatus('Playback failed. Retry or open the clip in a new tab.');
    });
}

function refreshAll() {
    loadEvents();
    loadHealth();
    loadStorage();
}

async function connectCamera(name) {
    const video = document.getElementById(`video-${name}`);
    const overlay = document.getElementById(`overlay-${name}`);

    overlay.textContent = 'Connecting to live feed…';
    state.live[name].webrtc = 'connecting';
    renderSystemStatus();
    renderCameraStatuses();

    if (peerConnections[name]) {
        peerConnections[name].close();
    }

    try {
        const pc = new RTCPeerConnection({
            iceServers: [{ urls: 'stun:stun.l.google.com:19302' }],
        });
        peerConnections[name] = pc;

        pc.ontrack = (event) => {
            video.srcObject = event.streams[0];
            overlay.classList.add('hidden');
            state.live[name].webrtc = 'online';
            renderSystemStatus();
            renderCameraStatuses();
        };

        pc.oniceconnectionstatechange = () => {
            if (pc.iceConnectionState === 'disconnected' || pc.iceConnectionState === 'failed') {
                overlay.textContent = 'Live feed dropped. Tap the card or use Reconnect.';
                overlay.classList.remove('hidden');
                state.live[name].webrtc = 'offline';
                renderSystemStatus();
                renderCameraStatuses();
            }
        };

        pc.addTransceiver('video', { direction: 'recvonly' });
        pc.addTransceiver('audio', { direction: 'recvonly' });

        const offer = await pc.createOffer();
        await pc.setLocalDescription(offer);
        await waitForIceGathering(pc);

        const response = await fetch(`${GO2RTC_BASE_URL}/api/webrtc?src=${encodeURIComponent(name)}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/sdp' },
            body: pc.localDescription.sdp,
        });

        if (!response.ok) {
            throw new Error(response.status === 501
                ? 'Live server does not support WebRTC on this origin.'
                : `HTTP ${response.status}`);
        }

        const answer = await response.text();
        await pc.setRemoteDescription({ type: 'answer', sdp: answer });
    } catch (error) {
        console.error(`Failed to connect ${name}:`, error);
        overlay.textContent = `${normalizeLiveError(error.message)} Tap the card or use Reconnect.`;
        overlay.classList.remove('hidden');
        state.live[name].webrtc = 'error';
        renderSystemStatus();
        renderCameraStatuses();
    }
}

function waitForIceGathering(pc) {
    return new Promise((resolve) => {
        if (pc.iceGatheringState === 'complete') {
            resolve();
            return;
        }

        const checkState = () => {
            if (pc.iceGatheringState === 'complete') {
                pc.removeEventListener('icegatheringstatechange', checkState);
                resolve();
            }
        };

        pc.addEventListener('icegatheringstatechange', checkState);
        setTimeout(resolve, 2000);
    });
}

async function loadEvents() {
    updateEventsMeta('Refreshing recent activity…');

    try {
        const response = await fetch(`${DETECTOR_BASE_URL}/api/events?limit=24`);
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }

        const payload = await response.json();
        state.eventsCache = payload.items || [];
        state.lastEventsRefreshAt = new Date();
        state.errors.events = null;
        reconcileSelection();
        renderEvents();
    } catch (error) {
        console.error('Failed to load events:', error);
        state.errors.events = normalizeApiError('Recent events', error.message);
        renderEvents();
    }
}

async function loadHealth() {
    try {
        const response = await fetch(`${DETECTOR_BASE_URL}/health`);
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }

        state.health = await response.json();
        state.lastHealthRefreshAt = new Date();
        state.errors.health = null;
    } catch (error) {
        console.error('Failed to load health:', error);
        state.errors.health = normalizeApiError('System health', error.message);
    }

    renderSystemStatus();
    renderCameraStatuses();
    renderOverview();
}

async function loadStorage() {
    try {
        const response = await fetch(`${DETECTOR_BASE_URL}/api/storage`);
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }

        state.storage = await response.json();
        state.lastStorageRefreshAt = new Date();
        state.errors.storage = null;
    } catch (error) {
        console.error('Failed to load storage:', error);
        state.errors.storage = normalizeApiError('Storage telemetry', error.message);
    }

    renderOverview();
}

function reconcileSelection() {
    const filteredEvents = getFilteredEvents();
    if (!filteredEvents.length) {
        state.selectedEventId = null;
        state.selectedEvent = null;
        return;
    }

    const selectedStillVisible = filteredEvents.some((event) => event.id === state.selectedEventId);
    if (!selectedStillVisible) {
        state.selectedEventId = filteredEvents[0].id;
    }
    state.selectedEvent = filteredEvents.find((event) => event.id === state.selectedEventId) || filteredEvents[0];
}

function getFilteredEvents() {
    return state.eventsCache.filter((event) => matchesActiveFilter(event));
}

function matchesActiveFilter(event) {
    if (state.activeFilter === 'all') {
        return true;
    }
    if (state.activeFilter === 'person') {
        return event.has_person;
    }
    if (state.activeFilter === 'motion') {
        return !event.has_person;
    }
    return event.camera_name === state.activeFilter;
}

function renderEvents() {
    const list = document.getElementById('events-list');
    const empty = document.getElementById('events-empty');
    const filteredEvents = getFilteredEvents();

    if (!filteredEvents.length) {
        list.innerHTML = '';
        empty.hidden = false;
        empty.textContent = state.errors.events
            ? state.errors.events
            : 'No events match the selected view.';
        renderDetail(null);
    } else {
        empty.hidden = true;
        list.innerHTML = filteredEvents.map(renderEventRow).join('');
        renderDetail(state.selectedEvent || filteredEvents[0]);
    }

    updateEventsMeta();
    renderOverview();
}

function renderEventRow(event) {
    const selected = event.id === state.selectedEventId;
    const badgeLabel = event.has_person ? 'Person' : 'Motion';
    const confidenceText = event.best_confidence
        ? formatConfidence(event.best_confidence)
        : 'Motion only';
    const previewUrl = detectorUrl(event.snapshot_url || event.thumbnail_url);
    const previewStyle = previewUrl
        ? ` style="background-image: linear-gradient(180deg, rgba(8, 15, 27, 0.1), rgba(8, 15, 27, 0.78)), url('${previewUrl}')"`
        : '';
    const metadata = [
        formatDuration(event.duration_seconds),
        confidenceText,
        event.crop?.active ? 'Cropped view' : '',
    ].filter(Boolean);

    return `
        <button class="event-row${selected ? ' selected' : ''}" data-event-id="${event.id}" type="button">
            <span class="event-preview"${previewStyle}></span>
            <span class="event-copy">
                <span class="event-row-top">
                    <span class="event-row-camera">${escapeHtml(event.camera_display_name)}</span>
                    <span class="event-row-badge ${event.has_person ? 'person' : 'motion'}">${badgeLabel}</span>
                </span>
                <span class="event-row-time">${escapeHtml(formatFullDate(event.started_at))}</span>
                <span class="event-row-meta">${metadata.map((item) => `<span>${escapeHtml(item)}</span>`).join('')}</span>
            </span>
        </button>
    `;
}

function renderDetail(event) {
    const empty = document.getElementById('detail-empty');
    const detail = document.getElementById('event-detail');

    if (!event) {
        detail.hidden = true;
        empty.hidden = false;
        empty.textContent = state.errors.events
            ? state.errors.events
            : 'Select an event to review its clip, metadata, and response actions.';
        state.selectedEvent = null;
        stopDetailPlayback();
        return;
    }

    state.selectedEvent = event;
    empty.hidden = true;
    detail.hidden = false;

    const badge = document.getElementById('detail-badge');
    document.getElementById('detail-camera').textContent = event.camera_display_name;
    document.getElementById('detail-title').textContent = event.has_person
        ? 'Person Event'
        : 'Motion Event';
    badge.textContent = event.has_person ? 'Person' : 'Motion';
    badge.className = `event-badge ${event.has_person ? 'person' : 'motion'}`;

    document.getElementById('detail-started-at').textContent = formatFullDate(event.started_at);
    document.getElementById('detail-duration').textContent = formatDuration(event.duration_seconds);
    document.getElementById('detail-confidence').textContent = event.best_confidence
        ? `${formatConfidence(event.best_confidence)} confidence`
        : 'No person detected';
    document.getElementById('detail-size').textContent = formatSizeMb(event.size_mb);
    document.getElementById('detail-note').textContent = buildDetailNote(event);

    const clipUrl = detectorUrl(event.clip_url);
    const openLink = document.getElementById('detail-open-link');
    const downloadLink = document.getElementById('detail-download-link');
    openLink.href = clipUrl;
    downloadLink.href = clipUrl;

    const video = document.getElementById('detail-video');
    video.poster = detectorUrl(event.snapshot_url || event.thumbnail_url);
    applyDetailPresentation(event);
    startDetailPlayback(event);
}

function startDetailPlayback(event) {
    const clipUrl = detectorUrl(event.clip_url);
    const video = document.getElementById('detail-video');

    stopDetailPlayback();
    state.playback.clipUrl = clipUrl;
    state.playback.error = null;

    if (!clipUrl) {
        showPlaybackStatus('No clip is available for this event.');
        return;
    }

    if (canPlayNativeHls(video)) {
        video.src = clipUrl;
        video.load();
        hidePlaybackStatus();
        return;
    }

    if (window.Hls && window.Hls.isSupported()) {
        const hls = new window.Hls({
            enableWorker: true,
            lowLatencyMode: false,
        });
        state.playback.hls = hls;

        hls.on(window.Hls.Events.ERROR, (_, data) => {
            if (!data.fatal) {
                return;
            }
            console.error('HLS playback failed:', data);
            showPlaybackStatus('Playback failed in this browser. Retry playback or open the clip in a new tab.');
            hls.destroy();
            state.playback.hls = null;
        });

        hls.attachMedia(video);
        hls.on(window.Hls.Events.MEDIA_ATTACHED, () => {
            hls.loadSource(clipUrl);
        });
        hidePlaybackStatus();
        return;
    }

    showPlaybackStatus('This browser cannot play HLS clips here. Use Open Clip or switch to Safari.');
}

function stopDetailPlayback() {
    const video = document.getElementById('detail-video');
    if (state.playback.hls) {
        state.playback.hls.destroy();
        state.playback.hls = null;
    }
    video.pause();
    video.removeAttribute('src');
    video.load();
}

function showPlaybackStatus(message) {
    state.playback.error = message;
    document.getElementById('detail-playback-message').textContent = message;
    document.getElementById('detail-playback-status').hidden = false;
}

function hidePlaybackStatus() {
    state.playback.error = null;
    document.getElementById('detail-playback-status').hidden = true;
}

function renderOverview() {
    const allEvents = state.eventsCache;
    const personCount = allEvents.filter((event) => event.has_person).length;
    const motionCount = allEvents.length - personCount;
    const latestEvent = allEvents[0];

    document.getElementById('events-summary-total').textContent = `${allEvents.length} loaded events`;
    document.getElementById('events-person-count').textContent = String(personCount);
    document.getElementById('events-motion-count').textContent = String(motionCount);
    document.getElementById('events-summary-latest').textContent = latestEvent
        ? `Latest: ${latestEvent.camera_display_name} at ${formatShortTime(latestEvent.started_at)}`
        : 'No recent activity yet.';

    const storageUsage = document.getElementById('storage-usage');
    const storageCaption = document.getElementById('storage-caption');
    const storageMeterFill = document.getElementById('storage-meter-fill');

    if (state.storage) {
        const percentage = Math.min((state.storage.usage_bytes / state.storage.limit_bytes) * 100, 100);
        storageUsage.textContent = `${state.storage.usage_gb} / ${state.storage.limit_gb} GB`;
        storageCaption.textContent = `${state.storage.event_count} managed events retained on disk`;
        storageMeterFill.style.width = `${percentage}%`;
        storageMeterFill.className = `storage-meter-fill${percentage > 85 ? ' warning' : ''}`;
    } else if (state.errors.storage) {
        storageUsage.textContent = 'Storage telemetry degraded';
        storageCaption.textContent = state.errors.storage;
        storageMeterFill.style.width = '0%';
        storageMeterFill.className = 'storage-meter-fill warning';
    }

    const alertTitle = document.getElementById('alert-title');
    const alertCaption = document.getElementById('alert-caption');
    if (!state.health) {
        alertTitle.textContent = state.errors.health ? 'Health degraded' : 'Checking active motion…';
        alertCaption.textContent = state.errors.health || 'Waiting for health data.';
        return;
    }

    const activeAlerts = state.health.cameras.filter((camera) => camera.event_active);
    if (activeAlerts.length) {
        alertTitle.textContent = `${activeAlerts.length} active alert${activeAlerts.length > 1 ? 's' : ''}`;
        alertCaption.textContent = `${activeAlerts.map((camera) => camera.display_name).join(', ')} currently recording motion.`;
    } else {
        alertTitle.textContent = 'No active alerts';
        alertCaption.textContent = 'All cameras are quiet right now.';
    }
}

function renderSystemStatus() {
    const banner = document.querySelector('.status-banner');
    const globalStatus = document.getElementById('global-status');
    const caption = document.getElementById('global-status-caption');
    const syncStatus = document.getElementById('last-sync-status');
    const liveOnlineCount = cameras.filter((camera) => state.live[camera].webrtc === 'online').length;
    const healthCameras = state.health?.cameras || [];
    const recorderOnlineCount = healthCameras.filter((camera) => camera.recorder_running).length;
    const activeAlerts = healthCameras.filter((camera) => camera.event_active).length;

    document.getElementById('live-online-count').textContent = `${liveOnlineCount} / ${cameras.length}`;
    document.getElementById('recorder-online-count').textContent = `${recorderOnlineCount} / ${cameras.length}`;

    let coverageTitle = 'Waiting for live state…';
    if (liveOnlineCount === cameras.length && recorderOnlineCount === cameras.length) {
        coverageTitle = 'All feeds and recorders are healthy';
    } else if (liveOnlineCount > 0 || recorderOnlineCount > 0) {
        coverageTitle = 'Monitoring is partially online';
    }
    if (state.errors.health) {
        coverageTitle = 'Health telemetry degraded';
    }
    document.getElementById('coverage-title').textContent = coverageTitle;

    banner.className = 'status-banner';
    if (state.errors.health) {
        banner.classList.add('status-banner-danger');
        globalStatus.textContent = 'Detector health is degraded';
        caption.textContent = state.errors.health;
    } else if (activeAlerts > 0) {
        banner.classList.add('status-banner-alert');
        globalStatus.textContent = `${activeAlerts} active alert${activeAlerts > 1 ? 's' : ''} in progress`;
        caption.textContent = 'Recordings are actively being captured. Review the event queue for details.';
    } else if (liveOnlineCount === cameras.length && recorderOnlineCount === cameras.length) {
        banner.classList.add('status-banner-good');
        globalStatus.textContent = 'Live watch is stable';
        caption.textContent = 'All live feeds and recorders are healthy.';
    } else if (liveOnlineCount > 0 || recorderOnlineCount > 0) {
        globalStatus.textContent = 'Monitoring is partially online';
        caption.textContent = `${liveOnlineCount}/${cameras.length} live feeds online and ${recorderOnlineCount}/${cameras.length} recorders ready.`;
    } else {
        globalStatus.textContent = 'Connecting live streams…';
        caption.textContent = 'Waiting for live feeds and recorder health to come online.';
    }

    const freshestRefresh = [state.lastEventsRefreshAt, state.lastHealthRefreshAt, state.lastStorageRefreshAt]
        .filter(Boolean)
        .sort((left, right) => right - left)[0];

    if (freshestRefresh) {
        syncStatus.textContent = `Updated ${freshestRefresh.toLocaleTimeString([], {
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit',
        })}`;
    } else {
        syncStatus.textContent = 'Waiting for the first refresh';
    }
}

function renderCameraStatuses() {
    const healthMap = new Map((state.health?.cameras || []).map((camera) => [camera.camera_name, camera]));

    cameras.forEach((cameraName) => {
        const health = healthMap.get(cameraName);
        const liveState = state.live[cameraName].webrtc;

        const pill = document.getElementById(`camera-pill-${cameraName}`);
        const liveValue = document.getElementById(`camera-${cameraName}-live`);
        const streamValue = document.getElementById(`camera-${cameraName}-stream`);
        const recorderValue = document.getElementById(`camera-${cameraName}-recorder`);
        const alertValue = document.getElementById(`camera-${cameraName}-alert`);
        const support = document.getElementById(`camera-${cameraName}-support`);

        liveValue.textContent = formatWebRtcState(liveState);
        streamValue.textContent = health ? (health.stream_connected ? 'Locked' : 'Issue') : 'Unknown';
        recorderValue.textContent = health ? (health.recorder_running ? 'Ready' : 'Stopped') : 'Unknown';
        alertValue.textContent = health ? (health.event_active ? 'Active' : 'Standby') : 'Unknown';

        if (health?.event_active) {
            pill.textContent = 'Recording Alert';
            pill.className = 'camera-pill alert';
        } else if (liveState === 'online' && health?.stream_connected && health?.recorder_running) {
            pill.textContent = 'Monitoring';
            pill.className = 'camera-pill good';
        } else if (liveState === 'connecting') {
            pill.textContent = 'Connecting';
            pill.className = 'camera-pill';
        } else if (liveState === 'error' || (health && (!health.stream_connected || !health.recorder_running))) {
            pill.textContent = 'Needs Attention';
            pill.className = 'camera-pill danger';
        } else {
            pill.textContent = 'Standing By';
            pill.className = 'camera-pill';
        }

        support.textContent = buildCameraSupport(cameraName, health, liveState);
        applyLiveCropPresentation(cameraName);
    });
}

function updateEventsMeta(forcedMessage = '') {
    const meta = document.getElementById('events-meta');
    if (forcedMessage) {
        meta.textContent = forcedMessage;
        return;
    }

    const filteredCount = getFilteredEvents().length;
    if (state.errors.events) {
        meta.textContent = state.errors.events;
        return;
    }

    const selectionText = state.selectedEventId ? `selected event #${state.selectedEventId}` : 'no event selected';
    meta.textContent = `${filteredCount} items in view, ${selectionText}.`;
}

function detectorUrl(path) {
    if (!path) {
        return '';
    }
    if (/^https?:\/\//.test(path)) {
        return path;
    }
    return `${DETECTOR_BASE_URL}${path}`;
}

function formatWebRtcState(stateValue) {
    if (stateValue === 'online') {
        return 'Online';
    }
    if (stateValue === 'connecting') {
        return 'Connecting';
    }
    if (stateValue === 'error') {
        return 'Error';
    }
    if (stateValue === 'offline') {
        return 'Offline';
    }
    return 'Waiting';
}

function toggleFullscreen(videoId) {
    const video = document.getElementById(videoId);
    const shell = video.closest('.video-shell') || video.closest('.detail-video-shell');
    const target = shell || video;
    if (document.fullscreenElement) {
        document.exitFullscreen();
    } else {
        target.requestFullscreen();
    }
}

function captureSnapshot(name) {
    const video = document.getElementById(`video-${name}`);
    if (!video.videoWidth || !video.videoHeight) {
        return;
    }

    const crop = getCameraCrop(name);
    const source = cropToSourceRect(video.videoWidth, video.videoHeight, crop);
    const canvas = document.createElement('canvas');
    canvas.width = source.width;
    canvas.height = source.height;

    const ctx = canvas.getContext('2d');
    ctx.drawImage(
        video,
        source.x,
        source.y,
        source.width,
        source.height,
        0,
        0,
        source.width,
        source.height,
    );

    const link = document.createElement('a');
    link.download = `${name}_${new Date().toISOString().replace(/[:.]/g, '-')}.jpg`;
    link.href = canvas.toDataURL('image/jpeg', 0.92);
    link.click();
}

function applyLiveCropPresentation(cameraName) {
    const shell = document.getElementById(`video-shell-${cameraName}`);
    const video = document.getElementById(`video-${cameraName}`);
    const health = getCameraHealth(cameraName);
    const crop = health?.crop;
    const width = video.videoWidth || health?.frame_width || 16;
    const height = video.videoHeight || health?.frame_height || 9;

    applyAspectRatio(shell, width, height, crop);
    if (crop?.active) {
        shell.classList.add('cropped-live-view');
        shell.style.setProperty('--crop-x', String(crop.x));
        shell.style.setProperty('--crop-y', String(crop.y));
        shell.style.setProperty('--crop-width', String(crop.width));
    } else {
        shell.classList.remove('cropped-live-view');
        shell.style.removeProperty('--crop-x');
        shell.style.removeProperty('--crop-y');
        shell.style.removeProperty('--crop-width');
    }
}

function applyDetailPresentation(event) {
    const shell = document.getElementById('detail-video-shell');
    const video = document.getElementById('detail-video');
    const metadata = event?.metadata || {};
    const crop = event?.crop || metadata.crop || null;
    const width = video.videoWidth || metadata.frame_width || 16;
    const height = video.videoHeight || metadata.frame_height || 9;

    applyAspectRatio(shell, width, height, null);
    shell.classList.toggle('cropped-media', Boolean(crop?.active));
}

function applyAspectRatio(shell, width, height, crop) {
    const croppedWidth = crop?.active ? width * crop.width : width;
    const croppedHeight = crop?.active ? height * crop.height : height;
    shell.style.setProperty('--display-aspect', `${Math.max(1, croppedWidth)} / ${Math.max(1, croppedHeight)}`);
}

function getCameraHealth(cameraName) {
    return (state.health?.cameras || []).find((camera) => camera.camera_name === cameraName);
}

function getCameraCrop(cameraName) {
    return getCameraHealth(cameraName)?.crop || null;
}

function cropToSourceRect(width, height, crop) {
    if (!crop?.active) {
        return { x: 0, y: 0, width, height };
    }
    return {
        x: Math.round(width * crop.x),
        y: Math.round(height * crop.y),
        width: Math.round(width * crop.width),
        height: Math.round(height * crop.height),
    };
}

function buildCameraSupport(cameraName, health, liveState) {
    const crop = health?.crop;
    if (health?.recorder_error) {
        return `Recorder error: ${health.recorder_error}`;
    }
    if (!health) {
        return 'Waiting for detector health data.';
    }
    if (liveState === 'error') {
        return 'Live server rejected the WebRTC request. Open the dashboard from the live server origin if needed.';
    }
    if (crop?.active) {
        return 'Bottom-half crop is active for live viewing, detections, snapshots, and saved clips.';
    }
    if (!health.stream_connected) {
        return 'Detector cannot read the stream right now. Check camera connectivity.';
    }
    if (!health.recorder_running) {
        return 'Recorder is not healthy. Check ffmpeg and go2rtc on the host.';
    }
    return 'Live feed, detector stream, and recorder are all ready.';
}

function buildDetailNote(event) {
    const parts = [];
    if (event.has_person) {
        parts.push('Person detection was confirmed for this event.');
    } else {
        parts.push('This event is motion-only.');
    }
    if (event.crop?.active) {
        parts.push('The saved clip is cropped to the bottom half of the camera view.');
    }
    parts.push('Open the clip in a new tab if inline playback fails.');
    return parts.join(' ');
}

function canPlayNativeHls(video) {
    return Boolean(
        video.canPlayType('application/vnd.apple.mpegurl')
        || video.canPlayType('application/x-mpegURL')
    );
}

function normalizeApiError(label, message) {
    if (!message) {
        return `${label} is unavailable.`;
    }
    if (message.includes('Failed to fetch')) {
        return `${label} is unavailable. Check the detector service on port 8000.`;
    }
    if (message.startsWith('HTTP 5')) {
        return `${label} returned a server error. Check the detector logs.`;
    }
    return `${label} is unavailable: ${message}`;
}

function normalizeLiveError(message) {
    if (!message) {
        return 'Unable to connect.';
    }
    if (message.includes('support WebRTC')) {
        return 'This page is not being served by the live server.';
    }
    return message;
}

function formatDuration(seconds) {
    if (!seconds) {
        return '0s';
    }

    const totalSeconds = Math.round(seconds);
    const minutes = Math.floor(totalSeconds / 60);
    const remainder = totalSeconds % 60;
    if (!minutes) {
        return `${remainder}s`;
    }
    return `${minutes}m ${remainder}s`;
}

function formatConfidence(confidence) {
    return `${Math.round(confidence * 100)}%`;
}

function formatSizeMb(sizeMb) {
    const value = Number(sizeMb);
    if (!Number.isFinite(value)) {
        return 'Unknown size';
    }
    return `${value.toFixed(1)} MB`;
}

function formatShortTime(timestamp) {
    return new Intl.DateTimeFormat(undefined, {
        hour: '2-digit',
        minute: '2-digit',
    }).format(new Date(timestamp));
}

function formatFullDate(timestamp) {
    return new Intl.DateTimeFormat(undefined, {
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
    }).format(new Date(timestamp));
}

function escapeHtml(value) {
    return String(value ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
}
