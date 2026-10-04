-- Earlier clients copied this fixed automatic label into the owner's alias.
-- Clear only that historical value once; later explicit names remain untouched.
UPDATE person_servers SET alias = ''
WHERE relation = 'owner' AND alias = '이 기기'
AND EXISTS (SELECT 1 FROM servers
  WHERE servers.server_id = person_servers.server_id
    AND servers.owner_person_id = person_servers.person_id
    AND servers.label = '이 기기');
