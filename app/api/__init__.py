def register_blueprints(app):
    from .auth import bp as auth_bp
    from .people import bp as people_bp
    from .academic import bp as academic_bp
    from .finance import bp as finance_bp
    from .dashboard import bp as dashboard_bp
    from .accounting import bp as accounting_bp
    from .integration import bp as integration_bp
    from .assets import bp as assets_bp
    from .payroll import bp as payroll_bp
    from .banking import bp as banking_bp

    for bp in (auth_bp, people_bp, academic_bp, finance_bp, dashboard_bp, accounting_bp,
               integration_bp, assets_bp, payroll_bp, banking_bp):
        app.register_blueprint(bp, url_prefix="/api")

    from .platform import bp as platform_bp
    app.register_blueprint(platform_bp)
