/**
 * Guided-tour steps of the admin pages of this platform.
 *
 * Loaded by the generic admin tutorial (``PlatformProvider.tutorial_scripts``); it adds its pages to
 * ``window.YS_PAGE_TUTORIALS``. Texts are English keys of the ``tutorials`` catalog. The pages share a
 * tour with the page they are a variant of.
 */
(function () {
    var tours = window.YS_PAGE_TUTORIALS = window.YS_PAGE_TUTORIALS || {};
    var aliases = {
        opinion_configuration_forum: 'opinion_configuration',
        prompts_forum: 'prompts'
    };
    Object.keys(aliases).forEach(function (page) {
        tours[page] = tours[aliases[page]];
    });
})();
