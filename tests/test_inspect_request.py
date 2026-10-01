from contextlib import contextmanager
from sqlalchemy import select, func
from app import cli
from app.models import Change, Audit
from test_portal import submit, Fake


def test_inspection_only_reads_and_keeps_request_status(client,db,monkeypatch,capsys):
    submit(client)
    count=db.scalar(select(func.count(Audit.id)))
    @contextmanager
    def session(): yield db
    provider=Fake()
    monkeypatch.setattr(cli,'SessionLocal',session)
    monkeypatch.setattr(cli,'WIW',lambda:provider)
    monkeypatch.setattr('sys.argv',['cli','inspect-request','--id','1'])
    cli.main()
    output=capsys.readouterr().out
    assert 'Matches pre-write state: True' in output
    assert 'Read-only inspection' in output
    assert db.get(Change,1).status=='pending'
    assert db.scalar(select(func.count(Audit.id)))==count
    assert provider.writes==0
