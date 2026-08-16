import { libcurl } from "https://esm.sh/libcurl.js?bundle=true&target=node";
await libcurl.load_wasm("https://cdn.jsdelivr.net/npm/libcurl.js@latest/libcurl.wasm")
libcurl.set_websocket(`wss://wisp.mercurywork.shop/`);

window.fetch = libcurl.fetch;
//

function getEndsAt(startTime, trackTimeMillis) {
  return startTime + trackTimeMillis;
}

async function searchITunes(artist, song, album, country = "CA") {
  const term = `${artist} ${song} ${album}`;

  const params = new URLSearchParams({
    term,
    country,
    media: "music",
    entity: "song",
    limit: "25"
  });

  const response = await fetch(
    `https://itunes.apple.com/search?${params.toString()}`
  );

  if (!response.ok) {
    throw new Error(`iTunes API error: ${response.status}`);
  }

  const data = await response.json();

  // Normalize strings for comparison
  const normalize = str =>
    str
      .toLowerCase()
      .replace(/[^\w\s]/g, "")
      .replace(/\s+/g, " ")
      .trim();

  const targetArtist = normalize(artist);
  const targetSong = normalize(song);
  const targetAlbum = normalize(album);

  // Score each result based on how closely it matches
  const results = data.results
    .map(track => {
      const resultArtist = normalize(track.artistName || "");
      const resultSong = normalize(track.trackName || "");
      const resultAlbum = normalize(track.collectionName || "");
//
      let score = 0;

      if (resultArtist === targetArtist) score += 40;
      if (resultSong === targetSong) score += 40;
      if (resultAlbum === targetAlbum) score += 20;

      return {
        score,
        track
      };
    })
    .sort((a, b) => b.score - a.score);

  return results;
}

/**
 * Fetches SiriusXM channel data from the MountainWrapper API.
 * Supports single channels or comma-separated lists.
 * 
 * Known legacy channel IDs (not display names):
 * - shade45 → Shade 45
 * - hiphopnation → Hip-Hop Nation
 * - totally70s → 70s on 7
 * - big80s → 80s on 8
 * - 60svibrations → 60s Gold
 * 
 * @param {string} channelIds - Comma-separated legacy channel IDs
 * @returns {Promise<Object>} - Parsed JSON response
 */
async function getSiriusXMChannels(channelIds) {
  const baseUrl = 'https://www.siriusxm.com/servlet/Satellite';
  const url = `${baseUrl}?pagename=SXM/Services/MountainWrapper&channels=${encodeURIComponent(channelIds)}&_cb=${Date.now()}`;
   
  try {
    const response = await fetch(url, {
      method: 'GET',
      cache: 'no-store',
      headers: {
        'Accept': 'application/json',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.0',
        'Cache-Control': 'no-cache, no-store, must-revalidate, max-age=0',
        'Pragma': 'no-cache',
      },
    });
    
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}: ${response.statusText}`);
    }
    
    const data = await response.json();
    console.log(data);
    return data;
  } catch (error) {
    console.error(`Failed to fetch channels [${channelIds}]:`, error.message);
    return { error: error.message, channels: {} };
  }
}

// === USAGE EXAMPLES ===

// Single channel
const data = await getSiriusXMChannels('totally70s')
console.log(await getCurrentSong(data.channels?.totally70s))

// Helper: Extract all channel IDs from a response
function extractChannelIds(responseData) {
  return Object.keys(responseData.channels || {});
}
function getRealTimes(startTime, endsAt) {
  return {
    startTime: new Date(startTime),
    endsAt: new Date(endsAt)
  };
}
// Helper: Get current song info for a channel
async function getCurrentSong(channelData) {
  const content = channelData?.content;
 
  if (!content || content.type !== 'Song') return null;
 
  const data  = {
    title: content.title,
    artist: content.artists?.[0]?.name,
    album: content.album?.title,
    duration: content.duration,
    art: content.album?.art,
    startTime: content.starttime
  };
  
 
   const itunesData = await searchITunes(data.artist, data.title, data.album)
    
   data.title = itunesData[0].track.trackName
 

  data.endsAt = getEndsAt(data.startTime,itunesData[0].track. trackTimeMillis)
  
  
  
  data.times = getRealTimes(data.startTime, data.endsAt )
   return data;
}

// Helper: Get schedule for a channel
function getSchedule(channelData) {
  const shows = channelData?.showSchedules?.shows || [];
  const schedules = channelData?.showSchedules?.schedules || [];
  
  return schedules.map(slot => {
    const show = shows.find(s => s.showId === slot.showId);
    return {
      showName: show?.showName || 'Unknown',
      startTime: slot.startTime,
      endTime: slot.endTime,
      durationMs: slot.duration
    };
  });
}///////
