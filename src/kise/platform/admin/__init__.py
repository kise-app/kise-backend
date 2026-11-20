"""The admin portal: server-rendered pages at ``/kise-office`` for managing accounts and plans.

It is a presentation adapter over Identity's ``AccountAdministrationService`` and lives in the
composition root because it also needs platform pieces (settings, the token signing key, a login
throttle) that no bounded context should own.

Who may sign in is Identity's ``Administrator`` aggregate. The first admin is created with
``python -m kise.platform.admin create`` (``cli.py``); admins then add each other in the portal.
"""
