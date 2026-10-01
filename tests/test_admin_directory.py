from app.models import User
from test_portal import sign_in


def test_directory_search_location_and_pagination(client,db):
    db.get(User,2).role='admin'
    for i in range(260):
        db.add(User(name=f'Crew {i:03}',email=f'crew{i}@example.com',password_hash='!',wiw_user_id=1000+i,
            role='employee',location='North',secondary_location='South' if i==123 else '',active=True))
    db.commit();sign_in(client,'manager@test.local')
    page=client.get('/admin')
    assert page.status_code==200
    assert page.text.count('class="person-row"')==25
    assert '263 people found' in page.text
    assert 'name="notification_email"' not in page.text
    found=client.get('/admin?q=crew123@example.com&location=South&role=employee&status=active')
    assert '1 person found' in found.text and 'Crew 123' in found.text
    assert 'Crew 122' not in found.text
    assert 'No matching employees' in client.get('/admin?q=doesnotexist').text
    assert 'No matching employees' in client.get('/admin?q=%25').text
    assert client.get('/admin?p=invalid').status_code==200
    assert 'Page 11 of 11' in client.get('/admin?p=999').text


def test_detail_access_and_return_context(client,db):
    sign_in(client)
    assert client.get('/admin/users/1').status_code==403
    db.get(User,2).role='admin';db.commit()
    csrf=sign_in(client,'manager@test.local')
    detail=client.get('/admin/users/1?q=employee&location=North&p=2')
    assert detail.status_code==200
    assert 'name="notification_email"' in detail.text
    assert 'q=employee&amp;location=North&amp;p=2' in detail.text
    assert client.get('/admin/users/999999').status_code==404
    response=client.post('/admin/users/1?q=employee&location=North',data={'csrf':csrf,'role':'employee','location':'North','notification_email':''})
    assert response.url.path=='/admin/users/1'
    assert response.url.params['q']=='employee'
    assert 'Access saved.' in response.text
