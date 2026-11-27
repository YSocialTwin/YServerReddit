/**
 * Guided-tour steps of the admin pages of this platform.
 *
 * Loaded by the generic admin tutorial (``PlatformProvider.tutorial_scripts``); it adds its pages to
 * ``window.YS_PAGE_TUTORIALS``. Texts are English keys of the ``tutorials`` catalog.
 */
(function () {
    var pages = {
        forum_image_resources: [
            { selector: '[data-tut="reusable-forum-image-resources"]', title: '@heading', description: 'Add image sources (feeds or subreddits) once and reuse them in every forum experiment. Validate a source before saving it.', position: 'auto' },
            { selector: '[data-tut="usage"]', title: '@heading', description: 'How the saved image resources are used by forum experiments.', position: 'auto' },
            { selector: '[data-tut="saved-image-resources"]', title: '@heading', description: 'The image resources already saved. Remove the ones you no longer need.', position: 'auto' }
        ],
        forum_rss_resources: [
            { selector: '[data-tut="reusable-forum-rss-resources"]', title: '@heading', description: 'Add RSS or Reddit feeds once and reuse them in every forum experiment. Validate a feed before saving it.', position: 'auto' },
            { selector: '[data-tut="usage"]', title: '@heading', description: 'How the saved feeds are used by forum experiments.', position: 'auto' },
            { selector: '[data-tut="saved-rss-resources"]', title: '@heading', description: 'The feeds already saved. Remove the ones you no longer need.', position: 'auto' }
        ]
    };
    window.YS_PAGE_TUTORIALS = window.YS_PAGE_TUTORIALS || {};
    Object.keys(pages).forEach(function (page) {
        window.YS_PAGE_TUTORIALS[page] = pages[page];
    });
})();
