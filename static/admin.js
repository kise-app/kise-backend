// Admin portal: the hamburger button.
//
// Wide screens: it collapses the sidebar to a rail of icons (remembered across pages).
// Narrow screens: it opens the sidebar as a drawer over the page; a tap outside, Escape, or
// choosing a page closes it. Without JavaScript the sidebar simply stays open, so every page is
// still reachable.
//
// Loaded in <head> without `defer`, so the remembered state is applied before the first paint and
// the sidebar does not flash open and then shut.
(function () {
  "use strict";

  var root = document.documentElement;
  var KEY = "kise-admin-sidebar";
  var wide = window.matchMedia("(min-width: 64rem)");

  root.classList.add("js");
  try {
    if (localStorage.getItem(KEY) === "collapsed") root.dataset.sidebar = "collapsed";
  } catch (e) {
    /* private mode: no memory, still works */
  }

  function collapsed() {
    return root.dataset.sidebar === "collapsed";
  }
  function drawerOpen() {
    return root.dataset.drawer === "open";
  }

  function sync() {
    var button = document.querySelector("[data-sidebar-toggle]");
    if (!button) return;
    var open = wide.matches ? !collapsed() : drawerOpen();
    button.setAttribute("aria-expanded", open ? "true" : "false");
    button.setAttribute(
      "aria-label",
      wide.matches ? (open ? "Collapse the menu" : "Expand the menu") : open ? "Close the menu" : "Open the menu"
    );
  }

  function setDrawer(open) {
    if (open) root.dataset.drawer = "open";
    else delete root.dataset.drawer;
    sync();
    if (open) {
      var first = document.querySelector("#admin-sidebar a");
      if (first) first.focus();
    }
  }

  function toggle() {
    if (wide.matches) {
      if (collapsed()) delete root.dataset.sidebar;
      else root.dataset.sidebar = "collapsed";
      try {
        localStorage.setItem(KEY, collapsed() ? "collapsed" : "open");
      } catch (e) {
        /* ignore */
      }
      sync();
    } else {
      setDrawer(!drawerOpen());
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    var button = document.querySelector("[data-sidebar-toggle]");
    if (button) button.addEventListener("click", toggle);
    document.querySelectorAll("[data-drawer-close]").forEach(function (el) {
      el.addEventListener("click", function () {
        setDrawer(false);
        if (button) button.focus();
      });
    });
    document.querySelectorAll("#admin-sidebar a").forEach(function (link) {
      link.addEventListener("click", function () {
        if (!wide.matches) setDrawer(false);
      });
    });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && drawerOpen()) {
        setDrawer(false);
        if (button) button.focus();
      }
    });
    wide.addEventListener("change", function () {
      setDrawer(false);
    });
    sync();
  });
})();
