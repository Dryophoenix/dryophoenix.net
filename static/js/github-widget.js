(function () {
  'use strict';

  // Everything below is interpolated into an innerHTML string, and all of it
  // (bios, repo names, descriptions, avatar URLs) is GitHub-hosted content
  // rather than anything this site controls. Escape text before it lands in
  // markup, and allow only http(s) in href/src so a `javascript:` value can't
  // ride in on an API field.
  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#x27;');
  }

  function safeUrl(s) {
    var url = String(s == null ? '' : s);
    return /^https?:\/\//i.test(url) ? esc(url) : '';
  }

  function q(url) {
    return fetch(url, {
      headers: { 'Accept': 'application/vnd.github+json' }
    }).then(function (r) {
      if (!r.ok) throw new Error('GitHub API returned ' + r.status);
      return r.json();
    });
  }

  function initWidget(box) {
    var u = box.dataset.username;
    if (!u) return;

    var api = 'https://api.github.com/users/' + encodeURIComponent(u);

    Promise.all([
      q(api),
      q(api + '/repos?sort=updated&per_page=12')
    ]).then(function (data) {
      var user = data[0];
      var repos = data[1]
        .filter(function (r) { return !r.fork; })
        .sort(function (a, b) { return b.stargazers_count - a.stargazers_count; })
        .slice(0, 6);

      var html =
        '<div class="gh-profile">' +
          '<img class="gh-avatar" src="' + safeUrl(user.avatar_url) + '" alt="' + esc(u) + '&#x27;s GitHub avatar" loading="lazy">' +
          '<div class="gh-info">' +
            '<a class="gh-name" href="' + safeUrl(user.html_url) + '" target="_blank" rel="noopener">' +
              esc(user.name || u) +
            '</a>' +
            (user.bio ? '<p class="gh-bio">' + esc(user.bio) + '</p>' : '') +
            '<div class="gh-stats">' +
              '<span>' + esc(user.public_repos) + ' repos</span>' +
              '<span>' + esc(user.followers) + ' followers</span>' +
              (user.location ? '<span>' + esc(user.location) + '</span>' : '') +
            '</div>' +
          '</div>' +
        '</div>';

      if (repos.length) {
        html += '<div class="gh-repos">';
        repos.forEach(function (r) {
          html +=
            '<a class="gh-repo" href="' + safeUrl(r.html_url) + '" target="_blank" rel="noopener">' +
              '<div class="gh-repo-name">' + esc(r.name) + '</div>' +
              (r.description ? '<div class="gh-repo-desc">' + esc(r.description) + '</div>' : '') +
              '<div class="gh-repo-meta">' +
                (r.language ? '<span class="gh-lang">' + esc(r.language) + '</span>' : '') +
                (r.stargazers_count ? '<span>&#9733; ' + esc(r.stargazers_count) + '</span>' : '') +
              '</div>' +
            '</a>';
        });
        html += '</div>';
      }

      box.innerHTML = html;
    }).catch(function (err) {
      console.error('GitHub widget failed for @' + u + ':', err);
      box.innerHTML =
        '<p class="gh-error">Could not load GitHub profile. ' +
        '<a href="https://github.com/' + encodeURIComponent(u) + '" target="_blank" rel="noopener">' +
        'View @' + esc(u) + ' directly →</a></p>';
    });
  }

  document.querySelectorAll('.github-widget[data-username]').forEach(initWidget);
}());
