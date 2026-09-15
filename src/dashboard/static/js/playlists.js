// =============================================================================
// playlists.js — Signal & Genre echo streams + Favorites
// =============================================================================
// Uses shared player template (otRenderPlayer + otSetPlaylist) from
// tuning-panel.js. All playback renders the same ot-player design.
// =============================================================================

const OT_GENRE_CDN = 'https://audio.lucidtuner.com/Chords_of_Truth_Echoes';

const SIGNAL_TYPES = [
    { name: 'Ground',  color: '#5ce1e6', desc: 'Peace, stillness, foundation' },
    { name: 'Clear',   color: '#a0ebff', desc: 'Clarity, focus, insight' },
    { name: 'Open',    color: '#e0b0ff', desc: 'Connection, expansion, heart' },
    { name: 'Rise',    color: '#ff6b5c', desc: 'Momentum, energy, growth' },
    { name: 'Raw',     color: '#ff8c00', desc: 'Courage, truth, edge' },
    { name: 'Bright',  color: '#ffd700', desc: 'Joy, radiance, play' },
    { name: 'Drive',   color: '#20b2aa', desc: 'Integration, purpose, will' },
];

const GENRE_ALBUMS = [
    { folder: 'Anthemic_Classic_Rock',   color: '#e74c3c', desc: 'Arena-ready, power chords, anthem',
      missing: ['Authenticity','Darkness_and_Light','Dreams','Faith','Guiding_Force','Listen','Love_Song','Moments','Pattern','Signs','The_Future','Training_Ground','Truth_and_Lies','Valley_of_Shadows','What_Life_Is_About','Wonder'] },
    { folder: 'Bluegrass_Trap_Fusion',   color: '#8bc34a', desc: 'Banjo meets 808, genre-bent' },
    { folder: 'Blues_Rock',              color: '#5c7cfa', desc: 'Gritty, soulful, electric' },
    { folder: 'Cali_Groove',            color: '#f9a825', desc: 'Laid-back, sun-soaked, vibes',
      missing: ['A_Good_Time','Guiding_Force','Listen','Love_Song','Moments','Pattern','Signs','The_Future','The_Passing_Tide','Truth_and_Lies','Tune_Your_Mind','Wonder'] },
    { folder: 'Celtic_Spirit',          color: '#26a69a', desc: 'Fiddle, tin whistle, homeland',
      missing: ['A_Good_Time','Darkness_and_Light','Listen','Love_Song','Moments','Signs','The_Future','The_Power_To_Be_Alive','Training_Ground','Truth_and_Lies','What_Life_Is_About','Wonder'] },
    { folder: 'Disco_Fever',            color: '#e040fb', desc: 'Funky, four-on-the-floor, sparkle' },
    { folder: 'Flowing',                color: '#80deea', desc: 'Gentle, liquid, continuous' },
    { folder: 'Folk_Ballad',            color: '#a1887f', desc: 'Storytelling, acoustic, tender' },
    { folder: 'Folktronica',            color: '#7c4dff', desc: 'Organic meets digital, textured' },
    { folder: 'Gypsy_Jazz',             color: '#ff8a65', desc: 'Django-inspired, hot club, swing',
      missing: ['Faith','Guiding_Force','Listen','Love_Song','Moments','Pattern','Signs','The_Future','The_Mirage','Training_Ground','Tune_Your_Mind','Valley_of_Shadows','What_Life_Is_About'] },
    { folder: 'Lo-Fi_Folk_Hop',         color: '#90a4ae', desc: 'Dusty beats, warm strings, chill' },
    { folder: 'Modern_Country',         color: '#c0ca33', desc: 'Nashville modern, twang, heart' },
    { folder: 'Neon_80s_Pop',           color: '#ff4081', desc: 'Synth-driven, retro, neon glow',
      missing: ['Authenticity','Faith','Guiding_Force','Listen','Love_Song','Moments','Signs','The_Future','The_Mirage','Training_Ground','Truth_and_Lies','Valley_of_Shadows','Wonder'] },
    { folder: 'Psychedelic_Funk',       color: '#ff6e40', desc: 'Wah-wah, groove, trip',
      missing: ['Authenticity','Darkness_and_Light','Dreams','Faith','Guiding_Force','Listen','Love_Song','Moments','Signs','The_Future','Training_Ground','Truth_and_Lies','Valley_of_Shadows','What_Life_Is_About'] },
    { folder: 'Shes_Got_the_Blues',     color: '#7986cb', desc: 'Torch songs, smoky, deep' },
    { folder: 'Smooth_Lounge_Jazz',     color: '#ffab40', desc: 'Velvet, sax, late-night' },
    { folder: 'Soaring_Cinematic_Folk', color: '#b39ddb', desc: 'Epic, sweeping, cinematic' },
    { folder: 'Swamp_Gospel',           color: '#6d4c41', desc: 'Muddy, spiritual, raw' },
    { folder: 'Symphonic_Dance',        color: '#ea80fc', desc: 'Orchestra meets EDM, grand' },
    { folder: 'Timeless_Soul',          color: '#ffcc80', desc: 'Motown warmth, golden era' },
];

let _playlistsLoaded = false;

async function loadPlaylistsTab() {
    const container = document.getElementById('playlistStreams');
    if (!container) return;
    if (_playlistsLoaded) return;
    _playlistsLoaded = true;

    // Load favorites for the favorites section
    if (typeof otLoadFavorites === 'function') await otLoadFavorites();

    let html = '';

    // ── Favorites Card ─────────────────────────────────────────────────
    html += '<div id="plFavSection">';
    html += _plRenderFavCard();
    html += '</div>';

    // ── Signal Streams ──────────────────────────────────────────────────
    html += '<div class="pl-section-title">Signal Streams</div>';
    html += '<div class="pl-grid">';
    SIGNAL_TYPES.forEach(sig => {
        const coverUrl = `https://audio.lucidtuner.com/Lucid_Tuner/${sig.name}_Signal/Cover.png`;
        html += `
            <button class="op-stream-card" onclick="playSignalStream('${sig.name}')" style="--stream-color: ${sig.color};">
                <div class="op-stream-cover" style="background-image: url('${coverUrl}');"></div>
                <div class="op-stream-info">
                    <div class="op-stream-name" style="color: ${sig.color};">${sig.name} Signal</div>
                    <div class="op-stream-desc">${sig.desc}</div>
                    <div class="op-stream-count">22 echoes</div>
                </div>
            </button>`;
    });
    html += '</div>';

    // ── Genre Albums ────────────────────────────────────────────────────
    html += '<div class="pl-section-title" style="margin-top:20px;">Genre Albums</div>';
    html += '<div class="pl-grid">';
    GENRE_ALBUMS.forEach(genre => {
        const coverUrl = OT_GENRE_CDN + '/' + genre.folder + '/Cover.png';
        const name = genre.folder.replace(/_/g, ' ');
        const trackCount = 22 - (genre.missing ? genre.missing.length : 0);
        html += `
            <button class="op-stream-card" onclick="playGenreAlbum('${genre.folder}')" style="--stream-color: ${genre.color};">
                <div class="op-stream-cover" style="background-image: url('${coverUrl}');"></div>
                <div class="op-stream-info">
                    <div class="op-stream-name" style="color: ${genre.color};">${name}</div>
                    <div class="op-stream-desc">${genre.desc}</div>
                    <div class="op-stream-count">${trackCount} echoes</div>
                </div>
            </button>`;
    });
    html += '</div>';

    // ── Player mount point (rendered by otRenderPlayer when a stream plays) ──
    html += '<div id="plPlayerMount" style="margin-top:16px;"></div>';

    container.innerHTML = html;
}

// ── Favorites Card ─────────────────────────────────────────────────────────

function _plRenderFavCard() {
    const favs = (typeof _otFavorites !== 'undefined') ? _otFavorites : [];
    const hasFavs = favs && favs.length > 0;
    const countText = hasFavs
        ? `${favs.length} echo${favs.length !== 1 ? 'es' : ''}`
        : 'Tap ♥ on any echo to save it here';

    return `
        <div class="pl-section-title">Favorites</div>
        <div class="pl-grid">
            <button class="op-stream-card${hasFavs ? '' : ' op-stream-empty'}" onclick="${hasFavs ? '_plPlayFavorites()' : ''}" style="--stream-color: #e74c3c;${hasFavs ? '' : ' opacity:0.6; cursor:default;'}">
                <div class="op-stream-cover" style="background: linear-gradient(135deg, #e74c3c 0%, #c0392b 100%); display:flex; align-items:center; justify-content:center;">
                    <span style="font-size:2rem;">${hasFavs ? '♥' : '♡'}</span>
                </div>
                <div class="op-stream-info">
                    <div class="op-stream-name" style="color: #e74c3c;">Favorites</div>
                    <div class="op-stream-desc">Your saved echoes</div>
                    <div class="op-stream-count">${countText}</div>
                </div>
            </button>
        </div>`;
}

function _plPlayFavorites() {
    const favs = (typeof _otFavorites !== 'undefined') ? _otFavorites : [];
    if (!favs || favs.length === 0) return;
    _plTrackEvent('playlist_play', { frequency: 'Favorites', echo_album: 'Favorites' });

    const tracks = favs.map(fav => {
        const folder = fav.folder || 'Raw_Signal';
        const isGenre = !folder.endsWith('_Signal');
        const display = folder.replace(/_Signal$/, '').replace(/_/g, ' ');
        const suffix = isGenre ? ' Echo' : ' Signal Echo';
        return {
            title: (fav.principle || fav.filename.replace(/_/g, ' ').replace('.mp3', '')) + ' (' + display + suffix + ')',
            filename: fav.filename,
            folder: folder,
            principle: fav.principle || '',
            cdnBase: isGenre ? OT_GENRE_CDN : undefined,
        };
    });

    // Shuffle all
    for (let i = tracks.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        [tracks[i], tracks[j]] = [tracks[j], tracks[i]];
    }

    if (typeof otSetPlaylist === 'function') {
        otSetPlaylist(tracks, {
            source: 'playlist',
            label: 'Favorites (' + tracks.length + ' echoes)',
            freqColor: '#e74c3c',
            autoplay: true,
            mountId: 'plPlayerMount'
        });

        const mount = document.getElementById('plPlayerMount');
        if (mount) mount.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
}

/** Refresh the favorites card (called after fav add/remove from player) */
function _plRefreshFavCard() {
    const section = document.getElementById('plFavSection');
    if (section) section.innerHTML = _plRenderFavCard();
}

// ── Activity Tracking (fire-and-forget) ────────────────────────────────
function _plTrackEvent(eventType, extra) {
    const body = Object.assign({ event_type: eventType, play_source: 'playlist' }, extra || {});
    try {
        fetch('/api/tuning/event', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
    } catch (e) { /* silent */ }
}

// ── Stream Playback (via shared player template) ────────────────────────────

function playSignalStream(signalName) {
    _plTrackEvent('playlist_play', { frequency: signalName + ' Signal', signal_type: signalName + '_Signal', echo_album: 'Signal Stream' });
    const folder = signalName + '_Signal';
    _plBuildAndPlay(folder, signalName + ' Signal', SIGNAL_TYPES.find(s => s.name === signalName)?.color);
}

function playGenreAlbum(folder) {
    _plTrackEvent('playlist_play', { frequency: folder.replace(/_/g, ' '), echo_album: 'Genre Album' });
    const genre = GENRE_ALBUMS.find(g => g.folder === folder);
    const name = folder.replace(/_/g, ' ');
    _plBuildGenreAndPlay(folder, name, genre?.color);
}

/** Build and play a signal stream (Lucid_Tuner CDN) */
function _plBuildAndPlay(folder, label, color) {
    let tracks;
    if (typeof otBuildTracks === 'function') {
        tracks = otBuildTracks(folder);
    } else {
        console.warn('[playlists] otBuildTracks not available');
        return;
    }

    // Shuffle
    for (let i = tracks.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        [tracks[i], tracks[j]] = [tracks[j], tracks[i]];
    }

    if (typeof otSetPlaylist === 'function') {
        otSetPlaylist(tracks, {
            source: 'playlist',
            label: label + ' Stream',
            freqColor: color || 'var(--accent)',
            autoplay: true,
            mountId: 'plPlayerMount'
        });

        const mount = document.getElementById('plPlayerMount');
        if (mount) mount.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
}

/** Build and play a genre album (Chords_of_Truth_Echoes CDN) */
function _plBuildGenreAndPlay(folder, label, color) {
    const display = folder.replace(/_/g, ' ');
    const genre = GENRE_ALBUMS.find(g => g.folder === folder);
    const missingSet = new Set(genre && genre.missing ? genre.missing : []);
    const tracks = OT_PRINCIPLES
        .filter(p => !missingSet.has(otSlugify(p)))
        .map(p => ({
            title: p + ' (' + display + ' Echo)',
            filename: otSlugify(p) + '_' + folder + '_Echo.mp3',
            folder: folder,
            principle: p,
            cdnBase: OT_GENRE_CDN,
        }));

    // Shuffle
    for (let i = tracks.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        [tracks[i], tracks[j]] = [tracks[j], tracks[i]];
    }

    if (typeof otSetPlaylist === 'function') {
        otSetPlaylist(tracks, {
            source: 'playlist',
            label: display + ' Album',
            freqColor: color || 'var(--accent)',
            autoplay: true,
            mountId: 'plPlayerMount'
        });

        const mount = document.getElementById('plPlayerMount');
        if (mount) mount.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
}


// =============================================================================
// Tune tab loader — renders tuning data into the Tune tab panel
// =============================================================================

// loadTuneTab removed — Tune tab now loads via loadTuneFlow() in tune-flow.js
// which uses the persistent otAudio player via _tfsInit/otSetPlaylist
