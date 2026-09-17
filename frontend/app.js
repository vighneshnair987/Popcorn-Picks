/* ==================================================================
   POPCORN PICKS — APPLICATION SCRIPT
   Sections: 1 State  2 Data  3 Init  4 Rendering  5 Search
             6 Filters  7 Sorting  8 Watchlist  9 Modal
             10 Analytics  11 Event listeners
   ================================================================== */

/* ==================================================================
   1. STATE
   ================================================================== */
const state = {
  allMovies: [],
  discoverResults: [],
  view: 'grid',              // 'grid' | 'list'
  sortBy: 'rating',
  searchTerm: '',
  filters: { genre: '', language: '', year: '', rating: '', certificate: '' },
  advFilters: { minRating: null, maxRating: null, yearFrom: null, yearTo: null, maxRuntime: null, minVotes: null },
  watchlist: [],              // array of movie ids
  activeMovieId: null,
  analytics: null,
  apiTotal: 0,
  loading: false,
  genres: [],
  languages: [],
  discoverRequestId: 0,
};

const GENRE_CLASS = {
  Action: 'genre-action', Romance: 'genre-romance', Thriller: 'genre-thriller',
  Comedy: 'genre-comedy', Horror: 'genre-horror', 'Sci-Fi': 'genre-scifi',
  Drama: 'genre-drama', Crime: 'genre-crime', Family: 'genre-family',
  Animation: 'genre-animation', Mystery: 'genre-mystery', Adventure: 'genre-adventure',
};
const GENRE_ICON = {
  Action: 'bi-lightning-charge', Romance: 'bi-heart', Thriller: 'bi-eye',
  Comedy: 'bi-emoji-laughing', Horror: 'bi-moon-stars', 'Sci-Fi': 'bi-rocket-takeoff',
  Drama: 'bi-mask', Crime: 'bi-shield-exclamation', Family: 'bi-people',
  Animation: 'bi-stars', Mystery: 'bi-search', Adventure: 'bi-compass',
};

function genreClass(genres) { return GENRE_CLASS[genres[0]] || 'genre-drama'; }
function genreIcon(genres) { return GENRE_ICON[genres[0]] || 'bi-film'; }

/* ==================================================================
  2. DATA
  ================================================================== */
const API_BASE = window.POPCORN_PICKS_API_URL || 'http://127.0.0.1:5000/api';

async function apiRequest(path, params = {}) {
  const query = new URLSearchParams(Object.entries(params).filter(([, value]) => value !== '' && value != null));
  const response = await fetch(`${API_BASE}${path}${query.toString() ? `?${query}` : ''}`);
  const body = await response.json();
  if (!response.ok || !body.success) throw new Error(body.error?.message || 'The API request failed.');
  return body;
}

async function fetchMovies(params = {}) {
  return (await apiRequest('/movies', { page: 1, limit: 100, sort: 'rating_desc', ...params })).data;
}
async function fetchMovieById(id) { return (await apiRequest(`/movies/${id}`)).data; }
async function searchMovies(term, params = {}) {
  return apiRequest('/search', { q: term, page: 1, limit: 100, ...params });
}
async function fetchGenres() { return (await apiRequest('/genres')).data; }
async function fetchLanguages() { return (await apiRequest('/languages')).data; }
async function fetchAnalytics() { return (await apiRequest('/analytics')).data; }
async function fetchTrending() { return (await apiRequest('/trending')).data; }

function displayValue(value, fallback = 'Unknown') { return value == null || value === '' ? fallback : value; }
function displayRating(value) { return value == null ? 'N/A' : Number(value).toFixed(1); }
function displayNumber(value) { return value == null ? 'N/A' : formatVotes(value); }
function normalizeMovie(movie) {
  return {
    ...movie,
    genres: Array.isArray(movie.genres) ? movie.genres : [],
    cast: Array.isArray(movie.cast) ? movie.cast : [],
  };
}

/* ==================================================================
   3. INITIALIZATION
   ================================================================== */
async function init() {
  const [movies, analytics, genres, languages] = await Promise.all([fetchMovies(), fetchAnalytics(), fetchGenres(), fetchLanguages()]);
  state.allMovies = movies.map(normalizeMovie);
  state.analytics = analytics;
  state.genres = genres;
  state.languages = languages;
  state.apiTotal = analytics.total_movies;
  state.discoverResults = [...state.allMovies];
  loadWatchlist();

  populateFilterOptions();
  renderHero();
  renderTonightsPicks();
  renderGenreExplorer();
  renderTrendInsights();
  applyDiscoverPipeline();
  renderWatchlist();
  renderAnalytics();
  updateWatchlistCount();

  document.getElementById('footerYear').textContent = `© ${new Date().getFullYear()} Popcorn Picks`;

  bindEvents();
}
document.addEventListener('DOMContentLoaded', init);

/* ==================================================================
   4. RENDERING
   ================================================================== */
function formatVotes(v) {
  if (v >= 1000) return (v / 1000).toFixed(v >= 10000 ? 0 : 1) + 'K';
  return String(v);
}

function movieCardHTML(m) {
  const inWatchlist = state.watchlist.includes(m.id);
  return `
    <article class="movie-card fade-up" data-id="${m.id}" tabindex="0" role="button" aria-label="View details for ${m.title}">
      <div class="movie-card-visual ${genreClass(m.genres)}">
        <span class="movie-card-rating"><i class="bi bi-star-fill"></i> ${displayRating(m.rating)}</span>
        <button class="watchlist-toggle ${inWatchlist ? 'active' : ''}" data-id="${m.id}" aria-label="${inWatchlist ? 'Remove from' : 'Add to'} watchlist" aria-pressed="${inWatchlist}">
          <i class="bi ${inWatchlist ? 'bi-bookmark-check-fill' : 'bi-bookmark-plus'}"></i>
        </button>
        <i class="bi ${genreIcon(m.genres)} movie-card-icon"></i>
      </div>
      <div class="movie-card-body">
        <div class="movie-card-title">${m.title}</div>
        <div class="movie-card-meta">
          <span>${displayValue(m.year)}</span><span class="sep">·</span><span>${displayValue(m.language)}</span><span class="sep">·</span><span>${displayNumber(m.votes)} votes</span>
        </div>
        <div class="movie-card-genres">${m.genres.map(g => `<span class="tag">${g}</span>`).join('')}</div>
      </div>
    </article>`;
}

function movieRowHTML(m) {
  const inWatchlist = state.watchlist.includes(m.id);
  return `
    <article class="movie-row fade-up" data-id="${m.id}" tabindex="0" role="button" aria-label="View details for ${m.title}">
      <div class="movie-row-visual ${genreClass(m.genres)}"></div>
      <div>
        <div class="movie-row-title">${m.title}</div>
        <div class="movie-row-meta">${displayValue(m.year)} · ${displayValue(m.language)} · ${m.genres.join(', ') || 'Uncategorized'}</div>
      </div>
      <div class="movie-row-rating"><i class="bi bi-star-fill"></i> ${displayRating(m.rating)}</div>
      <button class="watchlist-toggle ${inWatchlist ? 'active' : ''}" data-id="${m.id}" aria-label="${inWatchlist ? 'Remove from' : 'Add to'} watchlist" aria-pressed="${inWatchlist}" style="position:static;">
        <i class="bi ${inWatchlist ? 'bi-bookmark-check-fill' : 'bi-bookmark-plus'}"></i>
      </button>
    </article>`;
}

function renderMovies(container, movies, view = 'grid') {
  if (!movies.length) {
    container.innerHTML = `
      <div class="empty-state" style="grid-column: 1 / -1;">
        <i class="bi bi-emoji-frown"></i>
        <h3 class="font-display">No movies found.</h3>
        <p>Try widening your search or clearing a filter.</p>
      </div>`;
    container.classList.remove('compact');
    return;
  }
  container.classList.toggle('compact', view === 'list');
  container.innerHTML = movies.map(m => (view === 'list' ? movieRowHTML(m) : movieCardHTML(m))).join('');
}

function renderHero() {
  const featured = state.allMovies.find(movie => movie.rating != null) || state.allMovies[0];
  if (!featured) return;
  document.getElementById('heroFeaturedTitle').textContent = featured.title;
  document.getElementById('heroFeaturedMeta').textContent = `${displayValue(featured.year)} · ${displayValue(featured.language)} · ${displayValue(featured.runtime, 'N/A')} min`;
  document.getElementById('heroFeaturedTags').innerHTML = featured.genres.map(g => `<span class="tag">${g}</span>`).join('');
  document.querySelector('.hero-rating').innerHTML = `<i class="bi bi-star-fill"></i> ${displayRating(featured.rating)}`;

  document.getElementById('heroStatTotal').textContent = state.apiTotal || state.allMovies.length;
  document.getElementById('heroStatGenres').textContent = new Set(state.allMovies.flatMap(m => m.genres)).size;
  document.getElementById('heroStatLangs').textContent = state.analytics?.language_distribution?.length || new Set(state.allMovies.map(m => m.language).filter(Boolean)).size;
}

function renderTonightsPicks() {
  const picks = [...state.allMovies].filter(movie => movie.rating != null).sort((a, b) => b.rating - a.rating).slice(0, 8);
  renderMovies(document.getElementById('tonightsPicksGrid'), picks, 'grid');
}

function renderGenreExplorer() {
  const byGenre = {};
  state.allMovies.forEach(m => m.genres.forEach(g => {
    byGenre[g] = byGenre[g] || { count: 0, sum: 0 };
    byGenre[g].count++; byGenre[g].sum += m.rating;
  }));
  const grid = document.getElementById('genreGrid');
  grid.innerHTML = Object.entries(byGenre)
    .sort((a, b) => b[1].count - a[1].count)
    .map(([genre, d]) => `
      <div class="genre-card ${GENRE_CLASS[genre] || 'genre-drama'}" data-genre="${genre}" tabindex="0" role="button" aria-label="Filter by ${genre}">
        <div class="genre-card-content">
          <i class="bi ${GENRE_ICON[genre] || 'bi-film'}"></i>
          <h4 class="font-display">${genre}</h4>
          <div class="g-meta">${d.count} movies · ${(d.sum / d.count).toFixed(1)} avg rating</div>
        </div>
      </div>`).join('');
}

function renderTrendInsights() {
  const movies = state.allMovies;
  const byGenreCount = {};
  const byLangSum = {};
  const byYearCount = {};
  movies.forEach(m => {
    m.genres.forEach(g => byGenreCount[g] = (byGenreCount[g] || 0) + 1);
    byLangSum[m.language] = byLangSum[m.language] || { sum: 0, n: 0 };
    byLangSum[m.language].sum += m.rating; byLangSum[m.language].n++;
    byYearCount[m.year] = (byYearCount[m.year] || 0) + 1;
  });
  const topGenre = Object.entries(byGenreCount).sort((a, b) => b[1] - a[1])[0];
  const topLang = Object.entries(byLangSum).map(([l, d]) => [l, d.sum / d.n]).sort((a, b) => b[1] - a[1])[0];
  const topYear = Object.entries(byYearCount).sort((a, b) => b[1] - a[1])[0];
  const recentTop = [...movies].filter(m => m.year >= 2018).sort((a, b) => b.rating - a.rating)[0];
  const mostVoted = [...movies].sort((a, b) => b.votes - a.votes)[0];
  const decadeCount = {};
  movies.forEach(m => { const d = Math.floor(m.year / 10) * 10; decadeCount[d] = (decadeCount[d] || 0) + 1; });
  const topDecade = Object.entries(decadeCount).sort((a, b) => b[1] - a[1])[0];

  const insights = [
    { icon: 'bi-award', title: 'Highest-rated recent release', desc: `${recentTop.title} (${recentTop.year}) leads recent titles at ${recentTop.rating.toFixed(1)}.` },
    { icon: 'bi-tags', title: 'Most common genre', desc: `${topGenre[0]} appears in ${topGenre[1]} of the tracked titles.` },
    { icon: 'bi-translate', title: 'Best-rated language', desc: `${topLang[0]} titles average ${topLang[1].toFixed(1)} across the collection.` },
    { icon: 'bi-calendar3', title: 'Most productive year', desc: `${topYear[0]} has the most releases tracked so far, with ${topYear[1]} titles.` },
    { icon: 'bi-fire', title: 'Most-watched title', desc: `${mostVoted.title} leads by vote count with ${formatVotes(mostVoted.votes)} votes.` },
    { icon: 'bi-clock-history', title: 'Most common decade', desc: `The ${topDecade[0]}s account for the largest share of titles in the collection.` },
  ];
  document.getElementById('insightGrid').innerHTML = insights.map(i => `
    <div class="insight-card">
      <div class="insight-icon"><i class="bi ${i.icon}"></i></div>
      <div><h4>${i.title}</h4><p>${i.desc}</p></div>
    </div>`).join('');
}

/* ==================================================================
   5. SEARCH
   ================================================================== */
/* ==================================================================
   6. FILTERS
   ================================================================== */
function populateFilterOptions() {
  const genres = state.genres;
  const langs = state.languages;
  const years = [...new Set(state.allMovies.map(m => m.year))].sort((a, b) => b - a);
  const certs = [...new Set(state.allMovies.map(m => m.certificate))].sort();

  const fill = (id, values, fmt = v => v) => {
    const el = document.getElementById(id);
    values.forEach(v => { const o = document.createElement('option'); o.value = v; o.textContent = fmt(v); el.appendChild(o); });
  };
  fill('filterGenre', genres);
  fill('filterLanguage', langs);
  fill('filterYear', years);
  fill('filterCertificate', certs);

  const ratingSel = document.getElementById('filterRating');
  [9, 8, 7, 6, 5].forEach(r => { const o = document.createElement('option'); o.value = r; o.textContent = `${r}+`; ratingSel.appendChild(o); });
}

function filterMovies(movies) {
  const f = state.filters, adv = state.advFilters;
  return movies.filter(m => {
    if (f.genre && !m.genres.includes(f.genre)) return false;
    if (f.language && m.language !== f.language) return false;
    if (f.year && String(m.year) !== f.year) return false;
    if (f.rating && m.rating < Number(f.rating)) return false;
    if (f.certificate && m.certificate !== f.certificate) return false;
    if (adv.minRating != null && m.rating < adv.minRating) return false;
    if (adv.maxRating != null && m.rating > adv.maxRating) return false;
    if (adv.yearFrom != null && m.year < adv.yearFrom) return false;
    if (adv.yearTo != null && m.year > adv.yearTo) return false;
    if (adv.maxRuntime != null && m.runtime > adv.maxRuntime) return false;
    if (adv.minVotes != null && m.votes < adv.minVotes) return false;
    return true;
  });
}

/* ==================================================================
   7. SORTING
   ================================================================== */
function sortMovies(movies, sortBy) {
  const arr = [...movies];
  switch (sortBy) {
    case 'rating': return arr.sort((a, b) => b.rating - a.rating);
    case 'popularity': return arr.sort((a, b) => b.votes - a.votes);
    case 'newest': return arr.sort((a, b) => b.year - a.year);
    case 'oldest': return arr.sort((a, b) => a.year - b.year);
    case 'title': return arr.sort((a, b) => a.title.localeCompare(b.title));
    default: return arr;
  }
}

async function applyDiscoverPipeline() {
  const requestId = ++state.discoverRequestId;
  const f = state.filters, adv = state.advFilters;
  const sort = { rating: 'rating_desc', popularity: 'votes_desc', newest: 'year_desc', oldest: 'year_asc', title: 'title_asc' }[state.sortBy] || 'rating_desc';
  document.getElementById('resultCount').textContent = 'Searching...';
  try {
    const response = state.searchTerm
      ? await searchMovies(state.searchTerm, { genre: f.genre, language: f.language, year: f.year, year_from: adv.yearFrom, year_to: adv.yearTo, min_rating: f.rating || adv.minRating, min_votes: adv.minVotes, max_runtime: adv.maxRuntime, certificate: f.certificate, sort })
      : await apiRequest('/movies', { page: 1, limit: 100, genre: f.genre, language: f.language, year: f.year, year_from: adv.yearFrom, year_to: adv.yearTo, min_rating: f.rating || adv.minRating, min_votes: adv.minVotes, max_runtime: adv.maxRuntime, certificate: f.certificate, sort });
    if (requestId !== state.discoverRequestId) return;
    state.discoverResults = response.data.map(normalizeMovie);
    renderMovies(document.getElementById('discoverGrid'), state.discoverResults, state.view);
    document.getElementById('resultCount').textContent = `Showing ${response.pagination.total} result${response.pagination.total === 1 ? '' : 's'}`;
    return true;
  } catch (error) {
    if (requestId !== state.discoverRequestId) return;
    renderMovies(document.getElementById('discoverGrid'), []);
    document.getElementById('resultCount').textContent = error.message;
    return false;
  }
}

/* ==================================================================
   8. WATCHLIST
   ================================================================== */
function loadWatchlist() {
  try { state.watchlist = JSON.parse(localStorage.getItem('popcornpicks_watchlist') || '[]'); }
  catch { state.watchlist = []; }
}
function saveWatchlist() { localStorage.setItem('popcornpicks_watchlist', JSON.stringify(state.watchlist)); }

function toggleWatchlist(id) {
  id = Number(id);
  const idx = state.watchlist.indexOf(id);
  if (idx === -1) state.watchlist.push(id); else state.watchlist.splice(idx, 1);
  saveWatchlist();
  updateWatchlistCount();
  refreshWatchlistButtons();
  renderWatchlist();
  if (state.activeMovieId === id) syncModalWatchlistButton();
}

function updateWatchlistCount() {
  document.getElementById('watchlistCount').textContent = state.watchlist.length;
}

function refreshWatchlistButtons() {
  document.querySelectorAll('.watchlist-toggle').forEach(btn => {
    const id = Number(btn.dataset.id);
    const active = state.watchlist.includes(id);
    btn.classList.toggle('active', active);
    btn.setAttribute('aria-pressed', String(active));
    btn.innerHTML = `<i class="bi ${active ? 'bi-bookmark-check-fill' : 'bi-bookmark-plus'}"></i>`;
    btn.setAttribute('aria-label', `${active ? 'Remove from' : 'Add to'} watchlist`);
  });
}

function renderWatchlist() {
  const sortBy = document.getElementById('watchlistSort').value;
  let movies = state.allMovies.filter(m => state.watchlist.includes(m.id));
  if (sortBy === 'rating') movies = sortMovies(movies, 'rating');
  else if (sortBy === 'title') movies = sortMovies(movies, 'title');
  else movies = movies.sort((a, b) => state.watchlist.indexOf(b.id) - state.watchlist.indexOf(a.id));

  const grid = document.getElementById('watchlistGrid');
  const empty = document.getElementById('watchlistEmpty');
  if (!movies.length) { grid.innerHTML = ''; empty.style.display = 'block'; return; }
  empty.style.display = 'none';
  renderMovies(grid, movies, 'grid');
}

/* ==================================================================
   9. MODAL
   ================================================================== */
async function openMovieModal(id) {
  let m = state.allMovies.find(x => x.id === Number(id));
  try { m = normalizeMovie(await fetchMovieById(id)); }
  catch { if (!m) return; }
  if (!m) return;
  state.activeMovieId = m.id;

  document.getElementById('modalTitle').textContent = m.title;
  document.getElementById('modalMeta').textContent = `${displayValue(m.year)} · ${m.genres.join(', ') || 'Uncategorized'} · ${displayValue(m.language)}`;
  document.getElementById('modalRating').innerHTML = `<i class="bi bi-star-fill" style="color:var(--accent);"></i> ${displayRating(m.rating)}`;
  document.getElementById('modalVotes').textContent = displayNumber(m.votes);
  document.getElementById('modalRuntime').textContent = m.runtime == null ? 'N/A' : `${m.runtime} min`;
  document.getElementById('modalCert').textContent = displayValue(m.certificate);
  document.getElementById('modalDirector').textContent = displayValue(m.director);
  document.getElementById('modalCast').innerHTML = m.cast.map(c => `<span class="tag">${c}</span>`).join('');
  document.getElementById('modalDescription').textContent = displayValue(m.description);

  const visual = document.getElementById('modalVisual');
  visual.className = `modal-visual ${genreClass(m.genres)}`;

  syncModalWatchlistButton();

  const overlay = document.getElementById('movieModalOverlay');
  overlay.classList.add('open');
  document.body.style.overflow = 'hidden';
  document.getElementById('modalCloseBtn').focus();
}

function syncModalWatchlistButton() {
  const btn = document.getElementById('modalWatchlistBtn');
  const active = state.watchlist.includes(state.activeMovieId);
  btn.innerHTML = active
    ? `<i class="bi bi-bookmark-check-fill"></i> In Watchlist`
    : `<i class="bi bi-bookmark-plus"></i> Add to Watchlist`;
}

function closeMovieModal() {
  document.getElementById('movieModalOverlay').classList.remove('open');
  document.body.style.overflow = '';
  state.activeMovieId = null;
}

/* ==================================================================
   10. ANALYTICS
   ================================================================== */
function renderAnalytics() {
  const movies = state.allMovies;
  const avgRating = movies.reduce((s, m) => s + m.rating, 0) / movies.length;
  const highest = [...movies].sort((a, b) => b.rating - a.rating)[0];
  const genreCount = {};
  movies.forEach(m => m.genres.forEach(g => genreCount[g] = (genreCount[g] || 0) + 1));
  const topGenre = Object.entries(genreCount).sort((a, b) => b[1] - a[1])[0];
  const langCount = {};
  movies.forEach(m => langCount[m.language] = (langCount[m.language] || 0) + 1);
  const topLang = Object.entries(langCount).sort((a, b) => b[1] - a[1])[0];

  const stats = [
    { lbl: 'Total movies', val: movies.length, sub: 'tracked in this collection' },
    { lbl: 'Average rating', val: avgRating.toFixed(2), sub: 'across all titles' },
    { lbl: 'Highest rated', val: highest.title, sub: `${highest.rating.toFixed(1)} rating` },
    { lbl: 'Most common genre', val: topGenre[0], sub: `${topGenre[1]} titles` },
    { lbl: 'Most represented language', val: topLang[0], sub: `${topLang[1]} titles` },
  ];
  document.getElementById('analyticsStatGrid').innerHTML = stats.map(s => `
    <div class="stat-card"><div class="lbl">${s.lbl}</div><div class="val" style="${s.lbl==='Highest rated'||s.lbl==='Most common genre'||s.lbl==='Most represented language' ? 'font-size:1.15rem;' : ''}">${s.val}</div><div class="sub">${s.sub}</div></div>`).join('');

  renderCharts(movies);
}

const PLOTLY_BASE_LAYOUT = {
  paper_bgcolor: 'rgba(0,0,0,0)',
  plot_bgcolor: 'rgba(0,0,0,0)',
  font: { color: '#a7abb5', family: 'Inter, sans-serif', size: 11 },
  margin: { t: 10, r: 10, b: 36, l: 40 },
  xaxis: { gridcolor: 'rgba(255,255,255,0.06)', zerolinecolor: 'rgba(255,255,255,0.1)' },
  yaxis: { gridcolor: 'rgba(255,255,255,0.06)', zerolinecolor: 'rgba(255,255,255,0.1)' },
  showlegend: false,
};
const PLOTLY_CONFIG = { displayModeBar: false, responsive: true };
const ACCENT = '#e2a33d';
const ACCENT_PURPLE = '#8a6fd6';

function renderCharts(movies) {
  // Movies by year
  const byYear = {};
  movies.forEach(m => byYear[m.year] = (byYear[m.year] || 0) + 1);
  const years = Object.keys(byYear).sort();
  Plotly.newPlot('chartByYear', [{
    x: years, y: years.map(y => byYear[y]), type: 'bar', marker: { color: ACCENT },
  }], { ...PLOTLY_BASE_LAYOUT, height: 240 }, PLOTLY_CONFIG);

  // Average rating by year
  const ratingByYear = {};
  years.forEach(y => {
    const ms = movies.filter(m => String(m.year) === y);
    ratingByYear[y] = ms.reduce((s, m) => s + m.rating, 0) / ms.length;
  });
  Plotly.newPlot('chartRatingByYear', [{
    x: years, y: years.map(y => ratingByYear[y].toFixed(2)), type: 'scatter', mode: 'lines+markers',
    line: { color: ACCENT_PURPLE, width: 2 }, marker: { color: ACCENT_PURPLE, size: 6 },
  }], { ...PLOTLY_BASE_LAYOUT, height: 240, yaxis: { ...PLOTLY_BASE_LAYOUT.yaxis, range: [0, 10] } }, PLOTLY_CONFIG);

  // Genre distribution
  const genreCount = {};
  movies.forEach(m => m.genres.forEach(g => genreCount[g] = (genreCount[g] || 0) + 1));
  const genres = Object.keys(genreCount).sort((a, b) => genreCount[b] - genreCount[a]);
  Plotly.newPlot('chartGenreDist', [{
    labels: genres, values: genres.map(g => genreCount[g]), type: 'pie', hole: 0.55,
    marker: { colors: ['#e2a33d', '#8a6fd6', '#4fae7f', '#e2574c', '#5b8fd6', '#d67ab8', '#c9a34e', '#7a9dd6', '#d67a5b', '#8ad6b0', '#a38ad6', '#d6b98a'] },
    textfont: { color: '#f1efe9', size: 10 },
  }], { ...PLOTLY_BASE_LAYOUT, height: 260 }, PLOTLY_CONFIG);

  // Average rating by genre
  const ratingByGenre = {};
  genres.forEach(g => {
    const ms = movies.filter(m => m.genres.includes(g));
    ratingByGenre[g] = ms.reduce((s, m) => s + m.rating, 0) / ms.length;
  });
  Plotly.newPlot('chartRatingByGenre', [{
    x: genres.map(g => ratingByGenre[g].toFixed(2)), y: genres, type: 'bar', orientation: 'h', marker: { color: ACCENT },
  }], { ...PLOTLY_BASE_LAYOUT, height: 260, xaxis: { ...PLOTLY_BASE_LAYOUT.xaxis, range: [0, 10] } }, PLOTLY_CONFIG);

  // Language distribution
  const langCount = {};
  movies.forEach(m => langCount[m.language] = (langCount[m.language] || 0) + 1);
  const langs = Object.keys(langCount).sort((a, b) => langCount[b] - langCount[a]);
  Plotly.newPlot('chartLangDist', [{
    x: langs, y: langs.map(l => langCount[l]), type: 'bar', marker: { color: ACCENT_PURPLE },
  }], { ...PLOTLY_BASE_LAYOUT, height: 240 }, PLOTLY_CONFIG);

  // Rating distribution
  Plotly.newPlot('chartRatingDist', [{
    x: movies.map(m => m.rating), type: 'histogram', marker: { color: ACCENT }, xbins: { start: 6, end: 9, size: 0.25 },
  }], { ...PLOTLY_BASE_LAYOUT, height: 240 }, PLOTLY_CONFIG);

  // Top rated movies
  const top = [...movies].sort((a, b) => b.rating - a.rating).slice(0, 10).reverse();
  Plotly.newPlot('chartTopRated', [{
    x: top.map(m => m.rating), y: top.map(m => m.title), type: 'bar', orientation: 'h', marker: { color: ACCENT },
  }], { ...PLOTLY_BASE_LAYOUT, height: 320, xaxis: { ...PLOTLY_BASE_LAYOUT.xaxis, range: [0, 10] }, margin: { t: 10, r: 10, b: 36, l: 160 } }, PLOTLY_CONFIG);
}

function renderTrendInsights() {
  const data = state.analytics;
  if (!data) return;
  const insights = [
    { icon: 'bi-award', title: 'Highest-rated recent release', desc: data.highest_rated_recent_movies[0] ? `${data.highest_rated_recent_movies[0].title} (${displayValue(data.highest_rated_recent_movies[0].year)}) leads recent titles at ${displayRating(data.highest_rated_recent_movies[0].rating)}.` : 'No rated recent titles available.' },
    { icon: 'bi-tags', title: 'Most common genre', desc: data.most_common_genre ? `${data.most_common_genre.name} appears in ${data.most_common_genre.movies} tracked titles.` : 'No genre data available.' },
    { icon: 'bi-translate', title: 'Best-rated language', desc: data.best_rated_language ? `${data.best_rated_language.name} titles average ${displayRating(data.best_rated_language.average_rating)}.` : 'No language rating data available.' },
    { icon: 'bi-calendar3', title: 'Most productive year', desc: data.most_productive_year ? `${data.most_productive_year.year} has ${data.most_productive_year.movies} tracked titles.` : 'No year data available.' },
    { icon: 'bi-fire', title: 'Most-watched title', desc: data.popular_movies[0] ? `${data.popular_movies[0].title} leads with ${displayNumber(data.popular_movies[0].votes)} votes.` : 'No vote data available.' },
    { icon: 'bi-clock-history', title: 'Most common decade', desc: data.most_common_release_decade ? `The ${data.most_common_release_decade.decade}s account for the largest share of titles.` : 'No decade data available.' },
  ];
  document.getElementById('insightGrid').innerHTML = insights.map(i => `<div class="insight-card"><div class="insight-icon"><i class="bi ${i.icon}"></i></div><div><h4>${i.title}</h4><p>${i.desc}</p></div></div>`).join('');
}

function renderAnalytics() {
  const data = state.analytics;
  if (!data) return;
  const stats = [
    { lbl: 'Total movies', val: data.total_movies, sub: 'tracked in this collection' },
    { lbl: 'Average rating', val: displayValue(data.average_rating, 'N/A'), sub: `${data.rating_movie_count} rated titles` },
    { lbl: 'Highest rated', val: data.highest_rated_movie?.title || 'N/A', sub: data.highest_rated_movie ? `${displayRating(data.highest_rated_movie.rating)} rating` : 'No rating data' },
    { lbl: 'Most common genre', val: data.most_common_genre?.name || 'N/A', sub: data.most_common_genre ? `${data.most_common_genre.movies} titles` : 'No genre data' },
    { lbl: 'Most represented language', val: data.most_represented_language?.name || 'N/A', sub: data.most_represented_language ? `${data.most_represented_language.movies} titles` : 'No language data' },
  ];
  document.getElementById('analyticsStatGrid').innerHTML = stats.map(s => `<div class="stat-card"><div class="lbl">${s.lbl}</div><div class="val" style="${s.lbl==='Highest rated'||s.lbl==='Most common genre'||s.lbl==='Most represented language' ? 'font-size:1.15rem;' : ''}">${s.val}</div><div class="sub">${s.sub}</div></div>`).join('');
  renderCharts(data);
}

function renderCharts(data) {
  const byYear = data.movies_by_year;
  Plotly.newPlot('chartByYear', [{ x: byYear.map(row => row.year), y: byYear.map(row => row.count), type: 'bar', marker: { color: ACCENT } }], { ...PLOTLY_BASE_LAYOUT, height: 240 }, PLOTLY_CONFIG);
  const ratingByYear = data.average_rating_by_year.filter(row => row.average_rating != null);
  Plotly.newPlot('chartRatingByYear', [{ x: ratingByYear.map(row => row.year), y: ratingByYear.map(row => row.average_rating), type: 'scatter', mode: 'lines+markers', line: { color: ACCENT_PURPLE, width: 2 }, marker: { color: ACCENT_PURPLE, size: 6 } }], { ...PLOTLY_BASE_LAYOUT, height: 240, yaxis: { ...PLOTLY_BASE_LAYOUT.yaxis, range: [0, 10] } }, PLOTLY_CONFIG);
  const genres = data.genre_distribution;
  Plotly.newPlot('chartGenreDist', [{ labels: genres.map(row => row.genre), values: genres.map(row => row.movies), type: 'pie', hole: 0.55, marker: { colors: ['#e2a33d', '#8a6fd6', '#4fae7f', '#e2574c', '#5b8fd6', '#d67ab8'] }, textfont: { color: '#f1efe9', size: 10 } }], { ...PLOTLY_BASE_LAYOUT, height: 260 }, PLOTLY_CONFIG);
  const genreRatings = data.average_rating_by_genre.filter(row => row.average_rating != null);
  Plotly.newPlot('chartRatingByGenre', [{ x: genreRatings.map(row => row.average_rating), y: genreRatings.map(row => row.genre), type: 'bar', orientation: 'h', marker: { color: ACCENT } }], { ...PLOTLY_BASE_LAYOUT, height: 260, xaxis: { ...PLOTLY_BASE_LAYOUT.xaxis, range: [0, 10] } }, PLOTLY_CONFIG);
  const languages = data.language_distribution;
  Plotly.newPlot('chartLangDist', [{ x: languages.map(row => row.language), y: languages.map(row => row.movies), type: 'bar', marker: { color: ACCENT_PURPLE } }], { ...PLOTLY_BASE_LAYOUT, height: 240 }, PLOTLY_CONFIG);
  Plotly.newPlot('chartRatingDist', [{ x: data.rating_distribution.flatMap(row => Array(row.count).fill(Number(row.rating_band.split('-')[0]) + 0.5)), type: 'histogram', marker: { color: ACCENT }, xbins: { start: 0, end: 10, size: 1 } }], { ...PLOTLY_BASE_LAYOUT, height: 240 }, PLOTLY_CONFIG);
  const top = [...data.top_rated_movies].reverse();
  Plotly.newPlot('chartTopRated', [{ x: top.map(row => row.rating), y: top.map(row => row.title), type: 'bar', orientation: 'h', marker: { color: ACCENT } }], { ...PLOTLY_BASE_LAYOUT, height: 320, xaxis: { ...PLOTLY_BASE_LAYOUT.xaxis, range: [0, 10] }, margin: { t: 10, r: 10, b: 36, l: 160 } }, PLOTLY_CONFIG);
}

/* ==================================================================
   11. EVENT LISTENERS
   ================================================================== */
function bindEvents() {
  // Search (nav + discover, kept in sync)
  const navSearch = document.getElementById('navSearchInput');
  const discoverSearch = document.getElementById('discoverSearchInput');
  const discoverSearchForm = document.getElementById('discoverSearchForm');
  const onSearch = (val) => {
    state.searchTerm = val.trim();
    navSearch.value = state.searchTerm; discoverSearch.value = state.searchTerm;
    applyDiscoverPipeline();
    document.getElementById('discover').scrollIntoView({ behavior: 'smooth', block: 'start' });
  };
  navSearch.addEventListener('input', e => { discoverSearch.value = e.target.value; });
  navSearch.addEventListener('keydown', e => {
    if (e.key === 'Enter') onSearch(navSearch.value);
  });
  discoverSearch.addEventListener('input', e => { navSearch.value = e.target.value; });
  discoverSearchForm.addEventListener('submit', event => { event.preventDefault(); onSearch(discoverSearch.value); });

  // Filters
  ['filterGenre', 'filterLanguage', 'filterYear', 'filterRating', 'filterCertificate'].forEach(id => {
    document.getElementById(id).addEventListener('change', e => {
      const key = id.replace('filter', '').replace(/^\w/, c => c.toLowerCase());
      state.filters[key] = e.target.value;
      applyDiscoverPipeline();
    });
  });

  // Sort
  document.getElementById('sortSelect').addEventListener('change', e => { state.sortBy = e.target.value; applyDiscoverPipeline(); });

  // View toggle
  document.getElementById('gridViewBtn').addEventListener('click', () => setView('grid'));
  document.getElementById('listViewBtn').addEventListener('click', () => setView('list'));
  function setView(v) {
    state.view = v;
    document.getElementById('gridViewBtn').classList.toggle('active', v === 'grid');
    document.getElementById('listViewBtn').classList.toggle('active', v === 'list');
    document.getElementById('gridViewBtn').setAttribute('aria-pressed', String(v === 'grid'));
    document.getElementById('listViewBtn').setAttribute('aria-pressed', String(v === 'list'));
    applyDiscoverPipeline();
  }

  // Advanced filter drawer
  const advToggle = document.getElementById('advFilterToggle');
  const advDrawer = document.getElementById('advDrawer');
  advToggle.addEventListener('click', () => {
    const open = advDrawer.classList.toggle('open');
    advToggle.setAttribute('aria-expanded', String(open));
  });
  document.getElementById('advApply').addEventListener('click', () => {
    const num = id => { const v = document.getElementById(id).value; return v === '' ? null : Number(v); };
    state.advFilters = {
      minRating: num('advMinRating'), maxRating: num('advMaxRating'),
      yearFrom: num('advYearFrom'), yearTo: num('advYearTo'),
      maxRuntime: num('advRuntime'), minVotes: num('advMinVotes'),
    };
    applyDiscoverPipeline();
  });
  document.getElementById('advReset').addEventListener('click', () => {
    ['advMinRating', 'advMaxRating', 'advYearFrom', 'advYearTo', 'advRuntime', 'advMinVotes'].forEach(id => document.getElementById(id).value = '');
    state.advFilters = { minRating: null, maxRating: null, yearFrom: null, yearTo: null, maxRuntime: null, minVotes: null };
    applyDiscoverPipeline();
  });

  // Watchlist sort
  document.getElementById('watchlistSort').addEventListener('change', renderWatchlist);

  // Event delegation: movie cards / rows / genre cards / watchlist toggles
  document.addEventListener('click', (e) => {
    const wlBtn = e.target.closest('.watchlist-toggle');
    if (wlBtn) { e.stopPropagation(); toggleWatchlist(wlBtn.dataset.id); return; }

    const genreCard = e.target.closest('.genre-card');
    if (genreCard) {
      document.getElementById('filterGenre').value = genreCard.dataset.genre;
      state.filters.genre = genreCard.dataset.genre;
      applyDiscoverPipeline();
      document.getElementById('discover').scrollIntoView({ behavior: 'smooth', block: 'start' });
      return;
    }

    const card = e.target.closest('.movie-card, .movie-row');
    if (card) { openMovieModal(card.dataset.id); return; }
  });
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    const card = e.target.closest('.movie-card, .movie-row, .genre-card');
    if (card && document.activeElement === card) {
      e.preventDefault();
      card.click();
    }
  });

  // Modal
  document.getElementById('modalCloseBtn').addEventListener('click', closeMovieModal);
  document.getElementById('movieModalOverlay').addEventListener('click', (e) => {
    if (e.target.id === 'movieModalOverlay') closeMovieModal();
  });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeMovieModal(); });
  document.getElementById('modalWatchlistBtn').addEventListener('click', () => toggleWatchlist(state.activeMovieId));
  document.getElementById('modalAnalyticsBtn').addEventListener('click', closeMovieModal);

  // Nav watchlist icon → scroll to watchlist section
  document.getElementById('navWatchlistBtn').addEventListener('click', () => {
    document.getElementById('watchlist').scrollIntoView({ behavior: 'smooth', block: 'start' });
  });

  // Mobile menu
  const mobileBtn = document.getElementById('mobileMenuBtn');
  const mobilePanel = document.getElementById('mobilePanel');
  mobileBtn.addEventListener('click', () => {
    const isOpen = mobilePanel.style.display === 'block';
    mobilePanel.style.display = isOpen ? 'none' : 'block';
    mobileBtn.setAttribute('aria-expanded', String(!isOpen));
  });
  mobilePanel.addEventListener('click', (e) => { if (e.target.tagName === 'A') mobilePanel.style.display = 'none'; });

  // Active nav link highlighting on scroll
  const sections = ['home', 'discover', 'picks', 'genres', 'analytics', 'watchlist'].map(id => document.getElementById(id)).filter(Boolean);
  const navAnchors = document.querySelectorAll('.nav-links a');
  const observer = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        navAnchors.forEach(a => a.classList.toggle('active', a.getAttribute('href') === `#${entry.target.id}`));
      }
    });
  }, { rootMargin: '-40% 0px -50% 0px' });
  sections.forEach(s => observer.observe(s));
}
