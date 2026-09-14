import time, httpx
TENANT="a4941ae5-fcbb-4e45-85f2-fcbc3d7d7079"; CLIENT="1efdf29e-47c3-4f27-b423-50dadb4b48a3"
secret=open(r"C:\PROJEKTE\graph_secret.txt",encoding="utf-8-sig").read().strip()
def token():
    return httpx.post(f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token",data={"grant_type":"client_credentials","client_id":CLIENT,"client_secret":secret,"scope":"https://graph.microsoft.com/.default"},timeout=30).json()["access_token"]
for i in range(20):
    try:
        st=httpx.get("https://graph.microsoft.com/v1.0/users/fleing@schoeps.de/mailFolders",headers={"Authorization":f"Bearer {token()}"},params={"$top":"1","$select":"id"},timeout=30).status_code
    except Exception as e:
        st=f"netz:{type(e).__name__}"
    print(time.strftime("%H:%M:%S"),"fremdes Postfach ->",st,flush=True)
    if st==403: print("Policy greift.",flush=True); break
    time.sleep(90)
