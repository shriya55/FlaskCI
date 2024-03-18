import os
import urllib3
import socket
import tempfile
import fileinput
from requests.exceptions import ReadTimeout
from github import Github
from github.GithubException import UnknownObjectException, GithubException
from flask import Flask, jsonify , request

app = Flask(__name__)


class CreatePRAndAddLabel:
    API_TOKEN = ''
    newImageVersion = 'ocp-23-21'
    pr_url = None
    authorization = f'token {API_TOKEN}'
    num_retries = 10
    backoff_factor = 15
    github_client = Github(API_TOKEN)
    application_manifest_repo = "shriya55/helm-charts-ocp"
    git_commit_prefix = "feat"
    errored_messages = []
    repo = ""

    def __init__(self, comp_name):
        self.comp_name = comp_name
        self.branch_name = f"test-{self.comp_name}"
        self.file_path = f"manifests/{self.comp_name}/pre/immistable/values.yaml"
        print("inside Init")
        self.update_image_tag_and_raise_pr()

    def update_image_tag_and_raise_pr(self):
        repo = self.fetch_repository()
        file_content, pr_created, file_content_decoded = self.check_if_pr_exists_and_fetch_file_content(repo,
                                                                                                        self.file_path)
        new_file_content = getattr(self, "update_image_tag")(file_content=file_content_decoded, variable_key="imageTag",
                                                             new_image_version=self.newImageVersion)
        print(f"print new image value \n {new_file_content}")
        self.check_if_branch_exists(repo, pr_created)
        self.commit_to_branch(repo, file_content, new_file_content, self.file_path)
        self.create_pr(repo, pr_created)

    def fetch_repository(self):
        try:
            repo = self.github_client.get_repo(self.application_manifest_repo)
            print("Client Initiation Complete")
        except UnknownObjectException:
            error = f"[SKIPPING] Repo doesn't exist or have no access-{self.application_manifest_repo}"
            self.errored_messages.append(error)
            print(error)
        return repo

    def check_if_pr_exists_and_fetch_file_content(self, repo, file_path):
        if repo.archived:
            print(f"[SKIPPING] Archived repo - {self.application_manifest_repo}")

        file_content = repo.get_contents(file_path, ref=repo.default_branch)
        pr_created = False

        for pr in repo.get_pulls():
            if self.branch_name == pr.head.ref:
                try:
                    file_content = repo.get_contents(file_path, ref=pr.head.ref)
                    pr_created = True
                except UnknownObjectException:
                    print(f"[INFO] File wasn't found")
                    file_content_decoded = ""

        print(f"file content before decoding \n {file_content}")
        file_content_decoded = file_content.decoded_content.decode("utf-8")
        print(f"print old image value \n {file_content_decoded}")
        return file_content, pr_created, file_content_decoded

    @staticmethod
    def update_image_tag(**kwargs):
        content = kwargs['file_content']
        if not content:
            return ""

        content_lines = content.split("\n")
        i = 0
        while i < len(content_lines):
            print(f"content_lines[i] = {content_lines[i]}")
            if ':' in content_lines[i] and kwargs["variable_key"] in content_lines[i]:
                print(f"Update line content {content_lines[i]}")
                content_lines[i] = f" {kwargs['variable_key']}: {kwargs['new_image_version']}"
            i += 1
        content = "\n".join(content_lines)
        content = "\n".join(list(content.splitlines()))
        return content

    def check_if_branch_exists(self, repo, pr_created):
        branch_list = []
        for branch in list(repo.get_branches()):
            branch_list = branch_list[:len(branch_list)] + [branch.name]

        print(f"branch list {branch_list}")

        if not pr_created:
            if self.branch_name in branch_list:
                print(f"found a branch with no pr raised")
                repo.get_git_ref(f"heads/{self.branch_name}").delete()
                print(f"branch name which is removed {self.branch_name}")
                branch_list.remove(self.branch_name)
                print(f"branch list after removing a branch with no PR {branch_list}")

        if self.branch_name not in branch_list:
            try:
                print(f"creating new branch")
                repo_branch = repo.get_branch(repo.default_branch)
                repo.create_git_ref(ref=f'refs/heads/{self.branch_name}', sha=repo_branch.commit.sha)
                print(f"branch created")
            except UnknownObjectException as e:
                if "Not Found" in e.data['message']:
                    err = f"[SKIPPING] {repo.name} - branch unable to be created - most likely due to permissions or empty repo"
                    print(err)
                    self.errored_messages.append(err)

    def commit_to_branch(self, repo, file_content, new_file_content, file_path):
        git_method = "update_file"
        git_method_args = {
            "content": new_file_content,
            "path": file_path,
            "branch": self.branch_name,
            "message": f"{self.git_commit_prefix}: {self.branch_name} - Updating image tag for app{self.comp_name}",
            "sha": file_content.sha
        }

        getattr(repo, git_method)(**git_method_args)
        print(f"Updated content to branch")

    def create_pr(self, repo, pr_created):
        title = f"{self.git_commit_prefix}: {self.branch_name} - Update image tag for application {self.comp_name}"

        if not pr_created:
            try:
                print(f"Inside PR")
                pr = repo.create_pull(head=self.branch_name, base=repo.default_branch, title=title, body=title)
                print(f"PR Raised. URL is :" + pr.html_url)
                self.pr_url = pr.html_url
                self.add_labels(repo, pr)
            except (GithubException, socket.timeout, urllib3.exceptions.ReadTimeoutError, ReadTimeout) as e:
                print(f"PR creation timeout - ({repo.name}) - sleeping 60s")
                print(f"Details: {e}")

    def add_labels(self, repo, pr):
        labels = ["canary", "env: sit", "releaseName: test", f"appname: {self.comp_name}"]
        issue = repo.get_issue(number=pr.number)
        issue.set_labels(*labels)
        print(f"Added Labels to PR")


@app.route('/create_pr_and_add_label', methods=['GET'])
def create_pr_and_add_label():

    comp_name = request.args.get('comp_name')
    if not comp_name:
        return jsonify({"error": "Parameter 'comp_name' is missing"}), 400
    try:
        pr_label_creator = CreatePRAndAddLabel(comp_name)
        pr_label_creator.update_image_tag_and_raise_pr()
        if pr_label_creator.pr_url:
            return jsonify({
                "message": "PR and label creation process completed successfully. Please find the raised pull request URL : " + pr_label_creator.pr_url}), 200
        else:
            return jsonify({
                               "message": "PR and label creation process completed successfully. Please refer to existing PR for updates."}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    app.run(debug=True)
