import copy,importlib.util,sys,unittest
from pathlib import Path
source=Path(__file__).with_name("keeper-local-f03-v6.py")
spec=importlib.util.spec_from_file_location("local_v6_pure",source);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class Identity(unittest.TestCase):
    def setUp(self):
        self.state={"apiPid":100}
        def p(pid,parent,argv):return {"pid":pid,"ppid":parent,"created":1000+pid,"status":"sleeping","argv":argv}
        self.rows=[p(100,99,["python","-m","uvicorn"]),p(101,100,["python","-m","celery","beat"]),p(102,100,["python","-m","celery","worker"]),*[p(i,102,["python","-m","celery","worker"]) for i in range(103,107)]]
        self.raw={"pidfile":"101\n","beat":"beat: Starting... [beat] 父进程看门狗已启动（监视 PID=100）","worker":"clpm-remediation@host ready. [worker] 父进程看门狗已启动（监视 PID=100）","api":"Celery Beat 调度进程已启动 (PID=101) Celery Worker 进程已启动 (PID=102)"}
    def test_valid(self):self.assertEqual(m.startup_issues(self.state,self.rows,self.raw),[])
    def test_three_process_old_start_rejected(self):self.assertTrue(m.startup_issues(self.state,self.rows[:3],self.raw))
    def test_extra_or_duplicate_pid_rejected(self):
        for rows in (self.rows+self.rows[:1],self.rows[:-1]+self.rows[-2:-1]):self.assertTrue(m.startup_issues(self.state,rows,self.raw))
    def test_foreign_pool_rejected(self):
        rows=copy.deepcopy(self.rows);rows[-1]["ppid"]=999;self.assertTrue(m.startup_issues(self.state,rows,self.raw))
    def test_wrong_pool_command_rejected(self):
        rows=copy.deepcopy(self.rows);rows[-1]["argv"].append("foreign");self.assertTrue(m.startup_issues(self.state,rows,self.raw))
    def test_missing_wrong_or_stale_pidfile(self):
        for text in ("","102","101x","999"):
            self.assertTrue(m.startup_issues(self.state,self.rows,{**self.raw,"pidfile":text}))
    def test_uninitialized_beat_rejected(self):
        for text in ("","beat: Starting...","[beat] 父进程看门狗已启动（监视 PID=999）"):
            self.assertTrue(m.startup_issues(self.state,self.rows,{**self.raw,"beat":text}))
    def test_worker_not_ready_or_wrong_parent(self):
        for text in ("","ready.","x ready. [worker] 父进程看门狗已启动（监视 PID=999）"):
            self.assertTrue(m.startup_issues(self.state,self.rows,{**self.raw,"worker":text}))
    def test_respawn_or_wrong_initial_pid_rejected(self):
        for text in (self.raw["api"]+" Celery Beat 调度进程已启动 (PID=111)",self.raw["api"].replace("PID=101","PID=111"),self.raw["api"]+" Celery Worker 进程已启动 (PID=222)"):
            self.assertTrue(m.startup_issues(self.state,self.rows,{**self.raw,"api":text}))
    def test_identity_ignores_busy_state_but_preserves_full_birth_argv_parent(self):
        rows=copy.deepcopy(self.rows);rows[0]["status"]="running";self.assertTrue(m.same_process_identities(self.rows,rows))
        for key,value in (("pid",999),("ppid",999),("created",999),("argv",["foreign"])):
            rows=copy.deepcopy(self.rows);rows[-1][key]=value;self.assertFalse(m.same_process_identities(self.rows,rows))
if __name__=="__main__":unittest.main()
